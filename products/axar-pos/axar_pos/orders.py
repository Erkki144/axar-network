"""Server-side table orders and kitchen tickets (slice 4).

Invariants:
- one open order per table (partial unique index);
- every mutation of an open order is a compare-and-swap on ``version``, so two
  devices editing the same bill cannot silently overwrite each other (the
  loser gets 409 ``order_changed`` and must reload);
- quantities already sent to the kitchen cannot be reduced;
- "send to kitchen" is idempotent per key and only sends unsent quantities;
- paying an order creates the sale AND marks the order paid in ONE
  transaction (CAS ``status='open' AND version=?``), so the same bill can be
  paid at most once, from any number of devices.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import events, services
from .config import Settings
from .models import DiningTable, KitchenTicket, KitchenTicketLine, Order, OrderLine, Product, Refund, Sale
from .services import Conflict, DomainError, IdempotencyConflict, NotFound


class OrderChanged(Conflict):
    code = "order_changed"


class OrderNotOpen(Conflict):
    code = "order_not_open"


class NothingToSend(Conflict):
    code = "nothing_to_send"


class InvalidOrder(DomainError):
    status_code = 422
    code = "invalid_order"


# ---------------------------------------------------------------- read models


def list_tables(s: Session, tenant_id: str) -> list[DiningTable]:
    return list(s.scalars(select(DiningTable).where(
        DiningTable.tenant_id == tenant_id, DiningTable.active.is_(True)).order_by(DiningTable.code)))


def get_table(s: Session, tenant_id: str, table_id: int) -> DiningTable:
    t = s.scalar(select(DiningTable).where(DiningTable.id == table_id, DiningTable.tenant_id == tenant_id,
                                           DiningTable.active.is_(True)))
    if t is None:
        raise NotFound("table not found")
    return t


def open_order_for_table(s: Session, tenant_id: str, table_id: int) -> Order | None:
    return s.scalar(select(Order).where(Order.tenant_id == tenant_id, Order.table_id == table_id,
                                        Order.status.in_(("open", "partially_paid"))))


def get_order(s: Session, tenant_id: str, order_id: str) -> Order:
    o = s.scalar(select(Order).where(Order.id == order_id, Order.tenant_id == tenant_id)
                 .execution_options(populate_existing=True))
    if o is None:
        raise NotFound("order not found")
    return o


# ---------------------------------------------------------------- mutations


def _order_event(s: Session, o: Order, type_: str, now: datetime, version: int | None = None) -> None:
    events.emit(s, tenant_id=o.tenant_id, type=type_, payload={
        "order_id": o.id, "table_id": o.table_id, "status": "paid" if type_ == "order.closed" else o.status,
        "version": version or o.version}, aggregate_type="order", aggregate_id=o.id,
        aggregate_version=version or o.version, now=now, location_id=o.location_id)


def _ticket_event(s: Session, t: KitchenTicket, type_: str, now: datetime, version: int) -> None:
    events.emit(s, tenant_id=t.tenant_id, type=type_, payload={
        "ticket_id": t.id, "ticket_no": t.ticket_no, "order_id": t.order_id, "status": t.status},
        aggregate_type="kitchen_ticket", aggregate_id=t.id, aggregate_version=version, now=now,
        location_id=t.location_id)


def open_order(s: Session, tenant_id: str, table_id: int, cashier_id: str, now: datetime) -> Order:
    """Get-or-create the open order of a table (safe to call from many devices)."""
    table = get_table(s, tenant_id, table_id)
    existing = open_order_for_table(s, tenant_id, table_id)
    if existing is not None:
        return existing
    o = Order(id=str(uuid.uuid4()), tenant_id=tenant_id, location_id=table.location_id, table_id=table.id,
              status="open", version=1, opened_by=cashier_id, created_at=now, updated_at=now)
    s.add(o)
    _order_event(s, o, "order.opened", now)
    try:
        s.commit()
    except IntegrityError:  # another device opened it first
        s.rollback()
        existing = open_order_for_table(s, tenant_id, table_id)
        if existing is None:
            raise
        return existing
    return o


def _bump(s: Session, tenant_id: str, order_id: str, expected_version: int, now: datetime) -> None:
    """CAS on the order header; raises if the order changed or is not open."""
    res = s.execute(update(Order).where(
        Order.id == order_id, Order.tenant_id == tenant_id, Order.status == "open",
        Order.version == expected_version,
    ).values(version=Order.version + 1, updated_at=now).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        s.rollback()
        current = get_order(s, tenant_id, order_id)
        if current.status != "open":
            raise OrderNotOpen(f"order is {current.status}")
        raise OrderChanged(f"order changed on another device (version {current.version})")


def set_lines(s: Session, tenant_id: str, order_id: str, expected_version: int,
              lines: list[tuple[int, int]], now: datetime) -> Order:
    """Replace the order's lines with ``[(product_id, quantity)]``."""
    order = get_order(s, tenant_id, order_id)
    wanted: dict[int, int] = {}
    for pid, qty in lines:
        if pid in wanted:
            raise InvalidOrder(f"product {pid} listed twice")
        wanted[pid] = qty
    for pid in wanted:
        if s.scalar(select(Product.id).where(Product.id == pid, Product.tenant_id == tenant_id,
                                             Product.active.is_(True))) is None:
            raise NotFound(f"product {pid} not found")
    _bump(s, tenant_id, order_id, expected_version, now)
    current = {ln.product_id: ln for ln in order.lines}
    for pid, ln in current.items():
        if wanted.get(pid, 0) < ln.sent_quantity:
            s.rollback()
            raise InvalidOrder(f"cannot go below {ln.sent_quantity} already sent to the kitchen")
    for pid, ln in current.items():
        if pid not in wanted:
            s.delete(ln)
        else:
            ln.quantity = wanted[pid]
    for pid, qty in wanted.items():
        if pid not in current:
            s.add(OrderLine(order_id=order_id, tenant_id=tenant_id, product_id=pid, quantity=qty, sent_quantity=0))
    _order_event(s, order, "order.changed", now, version=expected_version + 1)
    s.commit()
    return get_order(s, tenant_id, order_id)


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def send_to_kitchen(s: Session, tenant_id: str, order_id: str, expected_version: int,
                    idempotency_key: str, now: datetime) -> tuple[KitchenTicket, bool]:
    """Create a ticket with the not-yet-sent quantities. Idempotent per key."""
    existing = s.scalar(select(KitchenTicket).where(KitchenTicket.tenant_id == tenant_id,
                                                    KitchenTicket.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.order_id != order_id:
            raise IdempotencyConflict("Idempotency-Key was already used for another order")
        return existing, True
    order = get_order(s, tenant_id, order_id)
    unsent = [ln for ln in order.lines if ln.quantity > ln.sent_quantity]
    if not unsent:
        raise NothingToSend("everything on this order was already sent")
    _bump(s, tenant_id, order_id, expected_version, now)
    no = services.next_number(s, tenant_id, "kitchen_ticket", KitchenTicket, KitchenTicket.ticket_no)
    ticket = KitchenTicket(id=str(uuid.uuid4()), tenant_id=tenant_id, location_id=order.location_id,
                           order_id=order_id, ticket_no=no, table_name=order.table.name, status="new",
                           idempotency_key=idempotency_key, created_at=now)
    s.add(ticket)
    for ln in unsent:
        name = s.scalar(select(Product.name).where(Product.id == ln.product_id))
        s.add(KitchenTicketLine(ticket_id=ticket.id, tenant_id=tenant_id, product_id=ln.product_id, name=name,
                                quantity=ln.quantity - ln.sent_quantity))
        ln.sent_quantity = ln.quantity
    _order_event(s, order, "order.changed", now, version=expected_version + 1)
    _ticket_event(s, ticket, "kitchen.ticket.created", now, version=1)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        again = s.scalar(select(KitchenTicket).where(KitchenTicket.tenant_id == tenant_id,
                                                     KitchenTicket.idempotency_key == idempotency_key))
        if again is not None and again.order_id == order_id:
            return again, True
        raise Conflict("concurrent kitchen send; reload the order and retry")
    s.refresh(ticket)
    return ticket, False


def list_tickets(s: Session, tenant_id: str, limit: int = 50) -> list[KitchenTicket]:
    return list(s.scalars(select(KitchenTicket).where(KitchenTicket.tenant_id == tenant_id)
                          .order_by(KitchenTicket.ticket_no.desc()).limit(limit)))


def get_ticket(s: Session, tenant_id: str, ticket_id: str) -> KitchenTicket:
    t = s.scalar(select(KitchenTicket).where(KitchenTicket.id == ticket_id, KitchenTicket.tenant_id == tenant_id))
    if t is None:
        raise NotFound("ticket not found")
    return t


def mark_ready(s: Session, tenant_id: str, ticket_id: str, now: datetime) -> KitchenTicket:
    t = s.scalar(select(KitchenTicket).where(KitchenTicket.id == ticket_id, KitchenTicket.tenant_id == tenant_id))
    if t is None:
        raise NotFound("ticket not found")
    if t.status != "ready":  # idempotent
        res = s.execute(update(KitchenTicket).where(KitchenTicket.id == t.id, KitchenTicket.status != "ready")
                        .values(status="ready", ready_at=now).execution_options(synchronize_session=False))
        if res.rowcount == 1:  # exactly one "ready" event even if two screens tap at once
            t = s.scalar(select(KitchenTicket).where(KitchenTicket.id == ticket_id)
                         .execution_options(populate_existing=True))
            _ticket_event(s, t, "kitchen.ticket.ready", now, version=2)
        s.commit()
    return t


def pay_order_cash(s: Session, *, tenant_id: str, order_id: str, expected_version: int, register_id: int,
                   cashier_id: str, tendered_cents: int, idempotency_key: str, settings: Settings,
                   now: datetime, actor_device_id: str | None = None,
                   actor_session_id: str | None = None) -> tuple[Sale, bool]:
    """Sale for the order's current lines + order -> paid, in ONE transaction."""
    # A replay with the same key returns the original sale even though the
    # order is already paid (lines of a paid order are immutable).
    order = get_order(s, tenant_id, order_id)
    if not order.lines:
        raise InvalidOrder("order is empty")
    # Lock order (C3): bill row first, then the drawer inside the sale — the
    # same order as split payments, so the two paths cannot deadlock.
    s.execute(select(Order.id).where(Order.id == order_id, Order.tenant_id == tenant_id).with_for_update())
    lines = [services.LineRequest(ln.product_id, ln.quantity) for ln in order.lines]

    def close_order(sess: Session, sale: Sale) -> None:
        res = sess.execute(update(Order).where(
            Order.id == order_id, Order.tenant_id == tenant_id, Order.status == "open",
            Order.version == expected_version,
        ).values(status="paid", sale_id=sale.id, version=Order.version + 1, updated_at=now)
            .execution_options(synchronize_session=False))
        if res.rowcount != 1:
            raise OrderChanged("order was changed or paid on another device")
        events.emit(sess, tenant_id=tenant_id, type="order.closed", payload={
            "order_id": order_id, "table_id": order.table_id, "status": "paid", "version": expected_version + 1,
            "sale_id": sale.id}, aggregate_type="order", aggregate_id=order_id,
            aggregate_version=expected_version + 1, now=now, location_id=order.location_id)

    return services.create_cash_sale(
        s, tenant_id=tenant_id, register_id=register_id, cashier_id=cashier_id,
        idempotency_key=idempotency_key, lines=lines, tendered_cents=tendered_cents,
        settings=settings, now=now, before_commit=close_order,
        hash_extra={"order_id": order_id, "order_version": expected_version},
        actor_device_id=actor_device_id, actor_session_id=actor_session_id,
    )


# ---------------------------------------------------------- operation status


def operation_status(s: Session, tenant_id: str, idempotency_key: str) -> dict:
    """Authoritative server answer: was a money operation with this key recorded?"""
    sale = s.scalar(select(Sale).where(Sale.tenant_id == tenant_id, Sale.idempotency_key == idempotency_key))
    if sale is not None:
        return {"status": "recorded", "kind": "sale", "id": sale.id}
    refund = s.scalar(select(Refund).where(Refund.tenant_id == tenant_id, Refund.idempotency_key == idempotency_key))
    if refund is not None:
        return {"status": "recorded", "kind": "refund", "id": refund.id}
    from .models import DrawerCashMovement, Payment

    cash = s.scalar(select(DrawerCashMovement).where(DrawerCashMovement.tenant_id == tenant_id,
                                                     DrawerCashMovement.idempotency_key == idempotency_key))
    if cash is not None:
        return {"status": "recorded", "kind": "cash_movement", "id": cash.id}

    pay = s.scalar(select(Payment).where(Payment.tenant_id == tenant_id, Payment.idempotency_key == idempotency_key))
    if pay is not None:
        return {"status": "recorded", "kind": "payment", "id": pay.id}
    return {"status": "not_recorded"}
