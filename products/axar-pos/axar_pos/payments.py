"""Bill payments (C3 on #15): open bills, mixed tenders, card terminal (MOCK),
bank-transfer receivables and bank matching.

Separate lifecycles — never "sale == payment":
- Bill (``orders``): open -> partially_paid -> paid | invoiced (the rest is a
  receivable) ; voided.
- Payment: cash captured at once; card pending -> captured | declined |
  unknown (answer lost -> reconcile with the provider); transfer pending until
  a bank line is matched -> captured.
- Sale (receipt, immutable): issued ONCE when payments + issued invoices
  cover the bill. VAT/revenue are recognized at the sale, not at cash receipt.
- Customer invoice: issued -> partially_paid -> paid (bank matching).

Money rules:
- Integer cents. Cash rounding (5 c) applies ONLY to the cash part that
  settles the final remainder; change comes only from cash tendered.
- Every operation locks the bill row first (then the drawer), so parallel
  devices serialize; ``expected_total_cents`` from the client protects
  against paying a bill that changed (409 ``bill_changed``).
- Every payment has an Idempotency-Key bound to its request hash; a retry
  returns the original payment.
- Invariant (tested): captured + pending transfer + reserved (card in flight)
  + remaining == bill total; never negative remaining, never overpaid.

NO real card processing: card goes to ``MockTerminalTxn``. Bank lines are
imported (sandbox), never fetched from a real bank here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import events, services
from .config import Settings
from .models import (
    BankTransaction,
    CustomerInvoice,
    DrawerSession,
    MockTerminalTxn,
    Order,
    Payment,
    Sale,
    SaleLine,
)
from .money import round_cash
from .services import Conflict, DomainError, IdempotencyConflict, NotFound


class BillChanged(Conflict):
    code = "bill_changed"


class BillClosed(Conflict):
    code = "bill_closed"


class Overpayment(DomainError):
    status_code = 422
    code = "overpayment"


class InvalidPayment(DomainError):
    status_code = 422
    code = "invalid_payment"


OPEN_STATES = ("open", "partially_paid")


# ------------------------------------------------------------ bill read model


@dataclass
class BillState:
    total: int
    captured: int
    pending_transfer: int
    reserved: int            # card payments pending/unknown
    remaining: int
    rounding: int
    payments: list[Payment]


def _lines(order: Order) -> list[services.LineRequest]:
    return [services.LineRequest(ln.product_id, ln.quantity) for ln in order.lines]


def bill_state(s: Session, tenant_id: str, order: Order, now: datetime, settings: Settings) -> BillState:
    if order.sale_id is not None:   # issued: the sale is the authority, prices never change afterwards
        total = s.scalar(select(Sale.total_cents).where(Sale.id == order.sale_id))
    elif order.lines:
        bdate = services.business_date_for(now, settings)
        total = sum(x.gross_cents for x in services._price_lines(s, tenant_id, _lines(order), bdate, None))
    else:
        total = 0
    pays = list(s.scalars(select(Payment).where(Payment.order_id == order.id, Payment.tenant_id == tenant_id)
                          .order_by(Payment.created_at, Payment.id).execution_options(populate_existing=True)))
    captured = sum(p.amount_cents for p in pays if p.status == "captured")
    pend_tr = sum(p.amount_cents for p in pays if p.method == "transfer" and p.status == "pending")
    reserved = sum(p.amount_cents for p in pays if p.method == "card" and p.status in ("pending", "unknown"))
    rounding = sum(p.rounding_cents for p in pays if p.status == "captured")
    return BillState(total, captured, pend_tr, reserved, total - captured - pend_tr - reserved, rounding, pays)


def bill_out(order: Order, st: BillState) -> dict:
    return {"order_id": order.id, "status": order.status, "version": order.version, "sale_id": order.sale_id,
            "total_cents": st.total, "paid_cents": st.captured, "pending_transfer_cents": st.pending_transfer,
            "in_flight_cents": st.reserved, "remaining_cents": st.remaining,
            "cash_rounding_cents": st.rounding,
            "payments": [{"id": p.id, "method": p.method, "status": p.status, "amount_cents": p.amount_cents,
                          "rounding_cents": p.rounding_cents, "tendered_cents": p.tendered_cents,
                          "change_cents": p.change_cents, "invoice_id": p.invoice_id}
                         for p in st.payments]}


# ------------------------------------------------------------------ helpers


def _lock_bill(s: Session, tenant_id: str, order_id: str) -> Order:
    """Bill row FIRST (lock order: bill -> drawer -> counters)."""
    o = s.scalar(select(Order).where(Order.id == order_id, Order.tenant_id == tenant_id).with_for_update()
                 .execution_options(populate_existing=True))
    if o is None:
        raise NotFound("bill not found")
    return o


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _existing(s: Session, tenant_id: str, key: str, req_hash: str) -> Payment | None:
    p = s.scalar(select(Payment).where(Payment.tenant_id == tenant_id, Payment.idempotency_key == key))
    if p is not None and p.request_hash != req_hash:
        raise IdempotencyConflict("Idempotency-Key was already used for a different payment")
    return p


def _open_drawer(s: Session, tenant_id: str, register_id: int) -> DrawerSession:
    services.get_register(s, tenant_id, register_id)
    d = services.get_open_drawer(s, tenant_id, register_id)
    if d is None:
        raise services.NoOpenDrawer("open this register's cash drawer first")
    return d


def _claim_cash(s: Session, tenant_id: str, drawer_id: int, cents: int) -> None:
    """Cash into an OPEN drawer (conditional UPDATE, like sales)."""
    res = s.execute(update(DrawerSession).where(DrawerSession.id == drawer_id, DrawerSession.tenant_id == tenant_id,
                                                DrawerSession.status == "open")
                    .values(cash_sales_cents=DrawerSession.cash_sales_cents + cents,
                            sale_count=DrawerSession.sale_count + 1)  # = cash takings (see ledger_cash_sales)
                    .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise services.NoOpenDrawer("the cash drawer was closed; payment not recorded")


def reference_number(base: int) -> str:
    """Estonian payment reference (viitenumber), 7-3-1 check digit."""
    digits = str(base)
    if len(digits) < 2:
        digits = digits.rjust(2, "0")
    weights = (7, 3, 1)
    total = sum(int(d) * weights[i % 3] for i, d in enumerate(reversed(digits)))
    return digits + str((10 - total % 10) % 10)


def valid_reference(ref: str) -> bool:
    return ref.isdigit() and 2 <= len(ref) <= 20 and reference_number(int(ref[:-1] or "0")) == ref


# ----------------------------------------------------------------- payments


def _common(s: Session, tenant_id: str, order_id: str, expected_total_cents: int, amount_cents: int, now: datetime,
            settings: Settings, key: str, req_hash: str) -> tuple[Order, BillState, Payment | None]:
    order = _lock_bill(s, tenant_id, order_id)
    # Re-check the key under the bill lock: a parallel request with the same
    # key that committed first must be replayed, not judged as overpayment.
    if (p := _existing(s, tenant_id, key, req_hash)) is not None:
        s.rollback()
        return order, None, p
    if order.status not in OPEN_STATES:
        raise BillClosed(f"bill is {order.status}")
    if not order.lines:
        raise InvalidPayment("the bill is empty")
    st = bill_state(s, tenant_id, order, now, settings)
    if st.total != expected_total_cents:
        raise BillChanged(f"the bill changed on another device (total now {st.total})")
    if amount_cents <= 0:
        raise InvalidPayment("amount must be positive")
    if amount_cents > st.remaining:
        raise Overpayment(f"amount exceeds the remaining {st.remaining} cents")
    return order, st, None


def _new_payment(order: Order, *, method: str, status: str, amount: int, register_id: int, key: str, req_hash: str,
                 actor: str, now: datetime, **kw) -> Payment:
    return Payment(id=str(uuid.uuid4()), tenant_id=order.tenant_id, order_id=order.id, method=method, status=status,
                   amount_cents=amount, register_id=register_id, idempotency_key=key, request_hash=req_hash,
                   created_by=actor, created_at=now, updated_at=now, **kw)


def _commit_payment(s: Session, tenant_id: str, order: Order, p: Payment, key: str, req_hash: str,
                    settings: Settings, now: datetime) -> tuple[Payment, bool]:
    s.add(p)
    s.flush()
    _after_change(s, tenant_id, order, now, settings, register_id=p.register_id, actor=p.created_by)
    try:
        s.commit()
    except IntegrityError:  # same key raced: return the winner
        s.rollback()
        again = _existing(s, tenant_id, key, req_hash)
        if again is None:
            raise
        return again, True
    return s.scalar(select(Payment).where(Payment.id == p.id).execution_options(populate_existing=True)), False


def pay_cash(s: Session, *, tenant_id: str, order_id: str, register_id: int, expected_total_cents: int,
             amount_cents: int, tendered_cents: int, idempotency_key: str, actor: str, settings: Settings,
             now: datetime) -> tuple[Payment, bool]:
    req = _hash({"op": "cash", "order": order_id, "register": register_id, "total": expected_total_cents,
                 "amount": amount_cents, "tendered": tendered_cents})
    if (p := _existing(s, tenant_id, idempotency_key, req)) is not None:
        return p, True
    order, st, again = _common(s, tenant_id, order_id, expected_total_cents, amount_cents, now, settings,
                               idempotency_key, req)
    if again is not None:
        return again, True
    final = amount_cents == st.remaining and st.reserved == 0
    if final:
        due = round_cash(amount_cents, settings.cash_rounding_step_cents)
    else:
        if amount_cents % settings.cash_rounding_step_cents:
            raise InvalidPayment("a partial cash amount must be a multiple of 5 cents")
        due = amount_cents
    if tendered_cents < due:
        raise services.InsufficientTender(f"tendered {tendered_cents} < due {due}")
    drawer = _open_drawer(s, tenant_id, register_id)
    _claim_cash(s, tenant_id, drawer.id, due)
    p = _new_payment(order, method="cash", status="captured", amount=amount_cents, register_id=register_id,
                     key=idempotency_key, req_hash=req, actor=actor, now=now, rounding_cents=due - amount_cents,
                     tendered_cents=tendered_cents, change_cents=tendered_cents - due, drawer_session_id=drawer.id)
    return _commit_payment(s, tenant_id, order, p, idempotency_key, req, settings, now)


def pay_card(s: Session, *, tenant_id: str, order_id: str, register_id: int, expected_total_cents: int,
             amount_cents: int, simulate: str, idempotency_key: str, actor: str, settings: Settings,
             now: datetime) -> tuple[Payment, bool]:
    """MOCK terminal. ``simulate``: approved | declined | timeout (the
    provider decides, but our answer is lost -> status unknown)."""
    if simulate not in ("approved", "declined", "timeout"):
        raise InvalidPayment("simulate must be approved, declined or timeout")
    req = _hash({"op": "card", "order": order_id, "register": register_id, "total": expected_total_cents,
                 "amount": amount_cents, "simulate": simulate})
    if (p := _existing(s, tenant_id, idempotency_key, req)) is not None:
        return p, True
    order, st, again = _common(s, tenant_id, order_id, expected_total_cents, amount_cents, now, settings,
                               idempotency_key, req)
    if again is not None:
        return again, True
    services.get_register(s, tenant_id, register_id)
    pid = str(uuid.uuid4())
    provider_result = "declined" if simulate == "declined" else "approved"
    s.add(MockTerminalTxn(payment_id=pid, tenant_id=tenant_id, amount_cents=amount_cents, result=provider_result,
                          created_at=now))
    status = {"approved": "captured", "declined": "declined", "timeout": "unknown"}[simulate]
    p = _new_payment(order, method="card", status=status, amount=amount_cents, register_id=register_id,
                     key=idempotency_key, req_hash=req, actor=actor, now=now, provider_ref=f"MOCK-{pid[:8]}")
    p.id = pid
    return _commit_payment(s, tenant_id, order, p, idempotency_key, req, settings, now)


def get_payment(s: Session, tenant_id: str, payment_id: str) -> Payment:
    p = s.scalar(select(Payment).where(Payment.id == payment_id, Payment.tenant_id == tenant_id))
    if p is None:
        raise NotFound("payment not found")
    return p


def reconcile_card(s: Session, *, tenant_id: str, payment_id: str, settings: Settings, now: datetime) -> Payment:
    """Ask the (mock) provider what really happened to an 'unknown' card payment."""
    p = s.scalar(select(Payment).where(Payment.id == payment_id, Payment.tenant_id == tenant_id))
    if p is None or p.method != "card":
        raise NotFound("card payment not found")
    order = _lock_bill(s, tenant_id, p.order_id)
    p = s.scalar(select(Payment).where(Payment.id == payment_id).execution_options(populate_existing=True))
    if p.status not in ("unknown", "pending"):
        s.rollback()
        return p
    txn = s.get(MockTerminalTxn, payment_id)
    p.status = "captured" if txn is not None and txn.result == "approved" else "declined"
    p.updated_at = now
    s.flush()
    _after_change(s, tenant_id, order, now, settings, register_id=p.register_id, actor=p.created_by)
    s.commit()
    return p


def pay_transfer(s: Session, *, tenant_id: str, order_id: str, register_id: int, expected_total_cents: int,
                 amount_cents: int, customer_name: str, customer_code: str | None, due_days: int,
                 idempotency_key: str, actor: str, settings: Settings, now: datetime) -> tuple[Payment, bool]:
    """Bank transfer = receivable. Nothing is 'paid' until a bank line matches."""
    req = _hash({"op": "transfer", "order": order_id, "register": register_id, "total": expected_total_cents,
                 "amount": amount_cents, "customer": customer_name, "code": customer_code, "due": due_days})
    if (p := _existing(s, tenant_id, idempotency_key, req)) is not None:
        return p, True
    if len(customer_name.strip()) < 2:
        raise InvalidPayment("customer name is required for an invoice")
    order, st, again = _common(s, tenant_id, order_id, expected_total_cents, amount_cents, now, settings,
                               idempotency_key, req)
    if again is not None:
        return again, True
    services.get_register(s, tenant_id, register_id)
    no = services.next_number(s, tenant_id, "customer_invoice", CustomerInvoice, CustomerInvoice.invoice_no)
    inv = CustomerInvoice(id=str(uuid.uuid4()), tenant_id=tenant_id, invoice_no=no, reference_no=reference_number(1000 + no),
                          order_id=order.id, customer_name=customer_name.strip(), customer_code=customer_code,
                          amount_cents=amount_cents, paid_cents=0, status="issued",
                          due_date=services.business_date_for(now, settings) + timedelta(days=due_days),
                          created_by=actor, created_at=now)
    s.add(inv)
    p = _new_payment(order, method="transfer", status="pending", amount=amount_cents, register_id=register_id,
                     key=idempotency_key, req_hash=req, actor=actor, now=now, invoice_id=inv.id)
    return _commit_payment(s, tenant_id, order, p, idempotency_key, req, settings, now)


# ------------------------------------------------------------ settle -> sale


def _after_change(s: Session, tenant_id: str, order: Order, now: datetime, settings: Settings, *,
                  register_id: int, actor: str) -> None:
    """Recompute the bill; issue the sale when fully covered; update status."""
    st = bill_state(s, tenant_id, order, now, settings)
    if st.remaining < 0:
        raise Overpayment("payments exceed the bill")  # defensive; rolls back the transaction
    issued = False
    if order.sale_id is None and st.remaining == 0 and st.reserved == 0:
        sale = _issue_sale(s, tenant_id, order, st, register_id, actor, now, settings)
        order.sale_id = sale.id
        issued = True
        for p in st.payments:
            if p.status in ("captured", "pending") and p.sale_id is None:
                p.sale_id = sale.id
        s.execute(update(CustomerInvoice).where(CustomerInvoice.order_id == order.id,
                                                CustomerInvoice.sale_id.is_(None)).values(sale_id=sale.id)
                  .execution_options(synchronize_session=False))
    new_status = ("paid" if order.sale_id and st.pending_transfer == 0 else
                  "invoiced" if order.sale_id else
                  "partially_paid" if st.captured or st.pending_transfer or st.reserved else "open")
    if new_status != order.status or issued:
        order.status = new_status
        order.version += 1
        order.updated_at = now
        # v2: bill lifecycle statuses (v1 knew only open / paid).
        events.emit(s, tenant_id=tenant_id, type="order.closed" if issued else "order.changed", version=2,
                    payload={"order_id": order.id, "table_id": order.table_id, "status": new_status,
                             "version": order.version, **({"sale_id": order.sale_id} if order.sale_id else {})},
                    aggregate_type="order", aggregate_id=order.id, aggregate_version=order.version, now=now,
                    location_id=order.location_id)


def _issue_sale(s: Session, tenant_id: str, order: Order, st: BillState, register_id: int, actor: str,
                now: datetime, settings: Settings) -> Sale:
    """The immutable receipt for the whole bill (VAT recognized here)."""
    drawer = _open_drawer(s, tenant_id, register_id)
    bdate = services.business_date_for(now, settings)
    sale_id = str(uuid.uuid4())
    lines = services._price_lines(s, tenant_id, _lines(order), bdate, sale_id)
    total = sum(x.gross_cents for x in lines)
    if total != st.total:
        raise BillChanged("prices changed while settling; reload the bill")
    vat = sum(x.vat_cents for x in lines)
    tenders = sorted({p.method for p in st.payments if p.status in ("captured", "pending")})
    cash = [p for p in st.payments if p.method == "cash" and p.status == "captured"]
    receipt_no = services.next_number(s, tenant_id, "receipt", Sale, Sale.receipt_no)
    from . import journal

    # Same numbering + journal as every other sale (O1a): per-register series,
    # hash-chained journal entry in this transaction.
    register_receipt_no, receipt_label = journal.next_register_receipt(
        s, tenant_id, services.get_register(s, tenant_id, register_id))
    sale = Sale(id=sale_id, tenant_id=tenant_id, receipt_no=receipt_no, register_id=register_id,
                register_receipt_no=register_receipt_no, receipt_label=receipt_label,
                drawer_session_id=drawer.id, cashier_id=actor, idempotency_key=f"bill:{order.id}",
                request_hash=_hash({"bill": order.id}), correlation_id=str(uuid.uuid4()), created_at=now,
                business_date=bdate, payment_method=tenders[0] if len(tenders) == 1 else "mixed",
                net_cents=total - vat, vat_cents=vat, total_cents=total, cash_rounding_cents=st.rounding,
                # cash part only (amount + rounding of cash legs); the drawer
                # ledger counts the cash payments themselves, not this sale.
                cash_due_cents=sum(p.amount_cents + p.rounding_cents for p in cash), tendered_cents=sum(p.tendered_cents for p in cash),
                change_cents=sum(p.change_cents for p in cash))
    s.add(sale)
    s.add_all(lines)
    s.flush()
    journal.append(s, tenant_id=tenant_id, stream_id=journal.stream_for_register(register_id), entry_type="sale",
                   entity_id=sale_id, summary=journal.sale_summary(sale, lines), now=now)
    from .models import RefundAllocation, SaleRefundState

    s.add_all(RefundAllocation(sale_line_id=ln.id, tenant_id=tenant_id, sale_id=sale_id, original_quantity=ln.quantity,
                               refunded_quantity=0, refunded_gross_cents=0, refunded_vat_cents=0) for ln in lines)
    s.add(SaleRefundState(sale_id=sale_id, tenant_id=tenant_id, refunded_gross_cents=0, refunded_cash_cents=0))
    payload = {
        "sale_id": sale.id, "receipt_no": sale.receipt_no, "register_id": register_id,
        "drawer_session_id": drawer.id, "order_id": order.id, "business_date": bdate.isoformat(),
        "currency": "EUR", "simulated": True, "payment_method": sale.payment_method,
        "total_cents": total, "vat_cents": vat, "net_cents": total - vat,
        "cash_due_cents": sale.cash_due_cents, "cash_rounding_cents": st.rounding,
        "tenders": [{"payment_id": p.id, "method": p.method, "status": p.status, "amount_cents": p.amount_cents,
                     "rounding_cents": p.rounding_cents, "invoice_id": p.invoice_id}
                    for p in st.payments if p.status in ("captured", "pending")],
        "lines": [{"sku": ln.sku, "quantity": ln.quantity, "unit_price_cents": ln.unit_price_cents,
                   "gross_cents": ln.gross_cents, "vat_rate_bp": ln.vat_rate_bp, "vat_cents": ln.vat_cents}
                  for ln in lines],
    }
    events.emit(s, tenant_id=tenant_id, type="pos.sale.completed", version=2, payload=payload,
                aggregate_type="sale", aggregate_id=sale.id, aggregate_version=1, now=now,
                correlation_id=sale.correlation_id, location_id=order.location_id)
    return sale


# ------------------------------------------------------------- bank matching


@dataclass
class BankLine:
    bank_tx_id: str
    booked_date: date
    amount_cents: int
    reference: str
    payer: str


def import_bank_lines(s: Session, *, tenant_id: str, lines: list[BankLine], settings: Settings,
                      now: datetime) -> list[dict]:
    """Sandbox bank statement import. A line is applied once (unique bank
    transaction id); it matches an invoice only by its exact reference
    number. Partial payments leave the rest outstanding; a surplus is kept
    as 'overpaid' for a manager, never silently absorbed."""
    out = []
    for ln in lines:
        if s.scalar(select(BankTransaction.id).where(BankTransaction.tenant_id == tenant_id,
                                                     BankTransaction.bank_tx_id == ln.bank_tx_id)) is not None:
            out.append({"bank_tx_id": ln.bank_tx_id, "status": "duplicate"})
            continue
        inv = None
        if ln.amount_cents > 0 and valid_reference(ln.reference.strip()):
            inv = s.scalar(select(CustomerInvoice).where(CustomerInvoice.tenant_id == tenant_id,
                                                         CustomerInvoice.reference_no == ln.reference.strip()))
        order = None
        if inv is not None:
            # Same lock order as tenders (bill FIRST, then its invoices), so a
            # bank import and a settlement of the same bill cannot deadlock.
            order = _lock_bill(s, tenant_id, inv.order_id)
            inv = s.scalar(select(CustomerInvoice).where(CustomerInvoice.id == inv.id).with_for_update()
                           .execution_options(populate_existing=True))
        allocated, status = 0, "unmatched"
        if inv is not None and inv.status != "paid":
            allocated = min(ln.amount_cents, inv.amount_cents - inv.paid_cents)
            inv.paid_cents += allocated
            inv.status = "paid" if inv.paid_cents == inv.amount_cents else "partially_paid"
            status = "overpaid" if ln.amount_cents > allocated else ("matched" if inv.status == "paid" else "partial")
            if inv.status == "paid":
                s.execute(update(Payment).where(Payment.invoice_id == inv.id, Payment.status == "pending")
                          .values(status="captured", updated_at=now).execution_options(synchronize_session=False))
                s.flush()
                if order.sale_id:
                    _after_change(s, tenant_id, order, now, settings, register_id=0, actor="bank")
        elif inv is not None:
            status = "overpaid"
        s.add(BankTransaction(tenant_id=tenant_id, bank_tx_id=ln.bank_tx_id, booked_date=ln.booked_date,
                              amount_cents=ln.amount_cents, reference=ln.reference.strip()[:35], payer=ln.payer[:200],
                              invoice_id=inv.id if inv else None, allocated_cents=allocated, status=status,
                              created_at=now))
        try:
            s.commit()
        except IntegrityError:  # concurrent import of the same bank line
            s.rollback()
            out.append({"bank_tx_id": ln.bank_tx_id, "status": "duplicate"})
            continue
        out.append({"bank_tx_id": ln.bank_tx_id, "status": status, "allocated_cents": allocated,
                    "invoice_id": inv.id if inv else None})
    return out


def invoices(s: Session, tenant_id: str) -> list[CustomerInvoice]:
    return list(s.scalars(select(CustomerInvoice).where(CustomerInvoice.tenant_id == tenant_id)
                          .order_by(CustomerInvoice.invoice_no.desc()).limit(200)))


def cash_in_payments(s: Session, tenant_id: str, drawer_id: int) -> int:
    return s.scalar(select(func.coalesce(func.sum(Payment.amount_cents + Payment.rounding_cents), 0)).where(
        Payment.tenant_id == tenant_id, Payment.drawer_session_id == drawer_id, Payment.method == "cash",
        Payment.status == "captured")) or 0
