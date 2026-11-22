"""Domain services. All functions take an explicit ``tenant_id`` and every query
filters on it — there is no code path that reads business rows unscoped."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    DrawerSession,
    OutboxEvent,
    Product,
    Refund,
    RefundAllocation,
    RefundLine,
    Register,
    Sale,
    SaleLine,
    TaxRate,
)
from .money import format_eur, round_cash, vat_from_gross


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class IdempotencyConflict(Conflict):
    code = "idempotency_key_reused"


class NoOpenDrawer(Conflict):
    code = "no_open_drawer"


class DrawerAlreadyOpen(Conflict):
    code = "drawer_already_open"


class InsufficientTender(DomainError):
    status_code = 422
    code = "insufficient_tender"


class TaxRateMissing(DomainError):
    status_code = 500
    code = "tax_rate_missing"


class LedgerMismatch(DomainError):
    status_code = 500
    code = "ledger_mismatch"


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"


class InvalidRefund(DomainError):
    status_code = 422
    code = "invalid_refund"


class OverRefund(Conflict):
    code = "over_refund"


class InsufficientDrawerCash(Conflict):
    code = "insufficient_drawer_cash"


ROLES = ("cashier", "manager", "owner")
REFUND_ROLES = ("manager", "owner")


# --------------------------------------------------------------------------- time


def business_date_for(moment: datetime, settings: Settings) -> date:
    local = moment.astimezone(ZoneInfo(settings.business_timezone))
    return (local - timedelta(hours=settings.business_day_cutoff_hour)).date()


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------- catalog


def list_products(s: Session, tenant_id: str) -> list[Product]:
    return list(
        s.scalars(
            select(Product)
            .where(Product.tenant_id == tenant_id, Product.active.is_(True))
            .order_by(Product.name)
        )
    )


def tax_rate_for(s: Session, category: str, on: date) -> int:
    rate = s.scalar(
        select(TaxRate.rate_bp)
        .where(
            TaxRate.category == category,
            TaxRate.valid_from <= on,
            (TaxRate.valid_to.is_(None)) | (TaxRate.valid_to >= on),
        )
        .order_by(TaxRate.valid_from.desc())
        .limit(1)
    )
    if rate is None:
        raise TaxRateMissing(f"no VAT rate for category '{category}' on {on}")
    return rate


# -------------------------------------------------------------------------- drawer


def list_registers(s: Session, tenant_id: str) -> list[Register]:
    return list(
        s.scalars(
            select(Register)
            .where(Register.tenant_id == tenant_id, Register.active.is_(True))
            .order_by(Register.code)
        )
    )


def get_register(s: Session, tenant_id: str, register_id: int) -> Register:
    r = s.scalar(
        select(Register).where(
            Register.id == register_id,
            Register.tenant_id == tenant_id,
            Register.active.is_(True),
        )
    )
    if r is None:
        raise NotFound("register not found")
    return r


def get_open_drawer(s: Session, tenant_id: str, register_id: int) -> DrawerSession | None:
    return s.scalar(
        select(DrawerSession).where(
            DrawerSession.tenant_id == tenant_id,
            DrawerSession.register_id == register_id,
            DrawerSession.status == "open",
        )
    )


def open_drawer(
    s: Session,
    tenant_id: str,
    register_id: int,
    cashier_id: str,
    float_cents: int,
    now: datetime,
) -> DrawerSession:
    get_register(s, tenant_id, register_id)
    if get_open_drawer(s, tenant_id, register_id) is not None:
        raise DrawerAlreadyOpen("this register already has an open drawer session")
    d = DrawerSession(
        tenant_id=tenant_id,
        register_id=register_id,
        opened_by=cashier_id,
        opened_at=now,
        opening_float_cents=float_cents,
        cash_sales_cents=0,
        sale_count=0,
        status="open",
    )
    s.add(d)
    try:
        s.commit()
    except IntegrityError:  # concurrent open on the same register lost the race
        s.rollback()
        raise DrawerAlreadyOpen("this register already has an open drawer session") from None
    return d


def ledger_cash_sales(s: Session, tenant_id: str, drawer_id: int) -> tuple[int, int]:
    """(Σ cash_due, count) recomputed from the immutable sales ledger."""
    total, n = s.execute(
        select(func.coalesce(func.sum(Sale.cash_due_cents), 0), func.count(Sale.id)).where(
            Sale.tenant_id == tenant_id,
            Sale.drawer_session_id == drawer_id,
            Sale.payment_method == "cash",
        )
    ).one()
    return int(total), int(n)


def ledger_cash_refunds(s: Session, tenant_id: str, drawer_id: int) -> tuple[int, int]:
    """(Σ cash_paid, count) recomputed from the immutable refunds ledger."""
    total, n = s.execute(
        select(func.coalesce(func.sum(Refund.cash_paid_cents), 0), func.count(Refund.id)).where(
            Refund.tenant_id == tenant_id,
            Refund.drawer_session_id == drawer_id,
            Refund.payment_method == "cash",
        )
    ).one()
    return int(total), int(n)


def _expected_cash_expr():
    return (
        DrawerSession.opening_float_cents
        + DrawerSession.cash_sales_cents
        - DrawerSession.cash_refunds_cents
    )


def get_drawer(s: Session, tenant_id: str, drawer_id: int) -> DrawerSession:
    d = s.scalar(
        select(DrawerSession).where(
            DrawerSession.id == drawer_id, DrawerSession.tenant_id == tenant_id
        )
    )
    if d is None:
        raise NotFound("drawer session not found")
    return d


def _claim_drawer(s: Session, tenant_id: str, drawer_id: int, cash_due: int) -> bool:
    """Atomically add a sale to a drawer *only if it is still open*.

    This single conditional UPDATE is the concurrency guard between sales and
    close: on PostgreSQL it takes the row lock and, under READ COMMITTED,
    re-evaluates ``status='open'`` after a concurrent close commits, so it
    matches 0 rows. On SQLite writers are already serialized (BEGIN
    IMMEDIATE). Either way a sale can never land in a closed drawer.
    """
    res = s.execute(
        update(DrawerSession)
        .where(
            DrawerSession.id == drawer_id,
            DrawerSession.tenant_id == tenant_id,
            DrawerSession.status == "open",
        )
        .values(
            cash_sales_cents=DrawerSession.cash_sales_cents + cash_due,
            sale_count=DrawerSession.sale_count + 1,
        )
        .execution_options(synchronize_session=False)
    )
    return res.rowcount == 1


def close_drawer(
    s: Session,
    tenant_id: str,
    drawer_id: int,
    cashier_id: str,
    counted_cents: int,
    note: str | None,
    now: datetime,
) -> DrawerSession:
    get_drawer(s, tenant_id, drawer_id)  # 404 for other tenants
    # Atomic close: expected is computed from the running total *inside* the
    # same UPDATE that flips the status, so no sale can slip in between.
    res = s.execute(
        update(DrawerSession)
        .where(
            DrawerSession.id == drawer_id,
            DrawerSession.tenant_id == tenant_id,
            DrawerSession.status == "open",
        )
        .values(
            status="closed",
            closed_by=cashier_id,
            closed_at=now,
            note=note,
            counted_cents=counted_cents,
            expected_cents=_expected_cash_expr(),
            variance_cents=counted_cents - _expected_cash_expr(),
        )
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        s.rollback()
        raise Conflict("drawer session is already closed")

    d = s.scalar(
        select(DrawerSession)
        .where(DrawerSession.id == drawer_id)
        .execution_options(populate_existing=True)
    )
    # Invariant: running total must equal the immutable ledger. If not, refuse
    # to close (rollback) — a human must investigate.
    ledger_total, ledger_n = ledger_cash_sales(s, tenant_id, drawer_id)
    if (ledger_total, ledger_n) != (d.cash_sales_cents, d.sale_count):
        s.rollback()
        raise LedgerMismatch(
            f"drawer running total {d.cash_sales_cents}/{d.sale_count} != "
            f"ledger {ledger_total}/{ledger_n}"
        )
    ref_total, ref_n = ledger_cash_refunds(s, tenant_id, drawer_id)
    if (ref_total, ref_n) != (d.cash_refunds_cents, d.refund_count):
        s.rollback()
        raise LedgerMismatch(
            f"drawer refund total {d.cash_refunds_cents}/{d.refund_count} != "
            f"ledger {ref_total}/{ref_n}"
        )
    s.commit()
    return d


# --------------------------------------------------------------------------- sales


@dataclass
class LineRequest:
    product_id: int
    quantity: int


def _request_hash(register_id: int, lines: list[LineRequest], tendered_cents: int) -> str:
    return _sha256(
        _canonical(
            {
                "register_id": register_id,
                "payment_method": "cash",
                "tendered_cents": tendered_cents,
                "lines": [[ln.product_id, ln.quantity] for ln in lines],
            }
        )
    )


def _find_by_key(s: Session, tenant_id: str, key: str) -> Sale | None:
    return s.scalar(
        select(Sale).where(Sale.tenant_id == tenant_id, Sale.idempotency_key == key)
    )


def _replay_or_conflict(existing: Sale, req_hash: str) -> tuple[Sale, bool]:
    if existing.request_hash != req_hash:
        raise IdempotencyConflict(
            "Idempotency-Key was already used for a different sale request"
        )
    return existing, True


def create_cash_sale(
    s: Session,
    *,
    tenant_id: str,
    register_id: int,
    cashier_id: str,
    idempotency_key: str,
    lines: list[LineRequest],
    tendered_cents: int,
    settings: Settings,
    now: datetime,
) -> tuple[Sale, bool]:
    """Create a cash sale on one register. Returns ``(sale, replayed)``.

    A retry with the same key and identical body returns the original sale and
    creates nothing. The same key with a different body is a 409.
    """
    req_hash = _request_hash(register_id, lines, tendered_cents)

    existing = _find_by_key(s, tenant_id, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, req_hash)

    get_register(s, tenant_id, register_id)
    drawer = get_open_drawer(s, tenant_id, register_id)
    if drawer is None:
        raise NoOpenDrawer("open this register's cash drawer before selling")

    bdate = business_date_for(now, settings)
    sale_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    sale_lines: list[SaleLine] = []
    for i, req in enumerate(lines, start=1):
        p = s.scalar(
            select(Product).where(
                Product.id == req.product_id,
                Product.tenant_id == tenant_id,
                Product.active.is_(True),
            )
        )
        if p is None:
            # Same response for "missing" and "belongs to another tenant".
            raise NotFound(f"product {req.product_id} not found")
        rate = tax_rate_for(s, p.tax_category, bdate)
        gross = p.price_cents * req.quantity
        vat = vat_from_gross(gross, rate)
        sale_lines.append(
            SaleLine(
                sale_id=sale_id,
                tenant_id=tenant_id,
                line_no=i,
                product_id=p.id,
                sku=p.sku,
                name=p.name,
                unit_price_cents=p.price_cents,
                quantity=req.quantity,
                gross_cents=gross,
                tax_category=p.tax_category,
                vat_rate_bp=rate,
                vat_cents=vat,
                net_cents=gross - vat,
            )
        )

    total = sum(ln.gross_cents for ln in sale_lines)
    vat_total = sum(ln.vat_cents for ln in sale_lines)
    cash_due = round_cash(total, settings.cash_rounding_step_cents)
    if tendered_cents < cash_due:
        raise InsufficientTender(
            f"tendered {format_eur(tendered_cents)} < due {format_eur(cash_due)}"
        )

    receipt_no = (
        s.scalar(select(func.coalesce(func.max(Sale.receipt_no), 0)).where(
            Sale.tenant_id == tenant_id
        ))
        + 1
    )

    # The read above may be stale (another request may have closed the drawer
    # since). The conditional UPDATE is authoritative.
    if not _claim_drawer(s, tenant_id, drawer.id, cash_due):
        s.rollback()
        raise NoOpenDrawer("the cash drawer was closed; sale not recorded")

    sale = Sale(
        id=sale_id,
        tenant_id=tenant_id,
        receipt_no=receipt_no,
        register_id=register_id,
        drawer_session_id=drawer.id,
        cashier_id=cashier_id,
        idempotency_key=idempotency_key,
        request_hash=req_hash,
        correlation_id=correlation_id,
        created_at=now,
        business_date=bdate,
        payment_method="cash",
        net_cents=total - vat_total,
        vat_cents=vat_total,
        total_cents=total,
        cash_rounding_cents=cash_due - total,
        cash_due_cents=cash_due,
        tendered_cents=tendered_cents,
        change_cents=tendered_cents - cash_due,
    )
    s.add(sale)
    s.add_all(sale_lines)
    s.add(_sale_completed_event(sale, sale_lines, now))
    try:
        s.flush()  # assigns sale line ids
        s.add_all(
            RefundAllocation(
                sale_line_id=ln.id,
                tenant_id=tenant_id,
                sale_id=sale_id,
                original_quantity=ln.quantity,
                refunded_quantity=0,
                refunded_gross_cents=0,
                refunded_vat_cents=0,
            )
            for ln in sale_lines
        )
        s.commit()
    except IntegrityError:
        # Concurrent request with the same key (or receipt number) won.
        s.rollback()
        existing = _find_by_key(s, tenant_id, idempotency_key)
        if existing is not None:
            return _replay_or_conflict(existing, req_hash)
        raise Conflict("concurrent sale conflict; retry with the same Idempotency-Key")
    s.refresh(sale)
    return sale, False


def _sale_completed_event(sale: Sale, lines: list[SaleLine], now: datetime) -> OutboxEvent:
    payload = {
        "sale_id": sale.id,
        "receipt_no": sale.receipt_no,
        "register_id": sale.register_id,
        "drawer_session_id": sale.drawer_session_id,
        "business_date": sale.business_date.isoformat(),
        "payment_method": sale.payment_method,
        "total_cents": sale.total_cents,
        "vat_cents": sale.vat_cents,
        "net_cents": sale.net_cents,
        "cash_due_cents": sale.cash_due_cents,
        "cash_rounding_cents": sale.cash_rounding_cents,
        "lines": [
            {
                "sku": ln.sku,
                "quantity": ln.quantity,
                "unit_price_cents": ln.unit_price_cents,
                "gross_cents": ln.gross_cents,
                "vat_rate_bp": ln.vat_rate_bp,
                "vat_cents": ln.vat_cents,
            }
            for ln in lines
        ],
    }
    body = _canonical(payload)
    return OutboxEvent(
        id=str(uuid.uuid4()),
        tenant_id=sale.tenant_id,
        type="pos.sale.completed",
        version=1,
        source="axar-pos",
        occurred_at=now,
        correlation_id=sale.correlation_id,
        payload=body,
        payload_hash=_sha256(body),
    )


def get_sale(s: Session, tenant_id: str, sale_id: str) -> Sale:
    sale = s.scalar(select(Sale).where(Sale.id == sale_id, Sale.tenant_id == tenant_id))
    if sale is None:
        raise NotFound("sale not found")
    return sale


def list_sales(s: Session, tenant_id: str, business_date: date | None = None) -> list[Sale]:
    q = select(Sale).where(Sale.tenant_id == tenant_id)
    if business_date is not None:
        q = q.where(Sale.business_date == business_date)
    return list(s.scalars(q.order_by(Sale.receipt_no)))


# ------------------------------------------------------------------------- refunds


@dataclass
class RefundLineRequest:
    line_no: int
    quantity: int


def refund_state(s: Session, tenant_id: str, sale_id: str) -> dict[int, RefundAllocation]:
    """sale_line_id -> allocation (refunded so far)."""
    rows = s.scalars(
        select(RefundAllocation).where(
            RefundAllocation.tenant_id == tenant_id, RefundAllocation.sale_id == sale_id
        )
    )
    return {r.sale_line_id: r for r in rows}


def _refund_request_hash(sale_id: str, register_id: int, lines: list[RefundLineRequest],
                         reason: str) -> str:
    return _sha256(_canonical({
        "sale_id": sale_id,
        "register_id": register_id,
        "payment_method": "cash",
        "reason": reason,
        "lines": sorted([ln.line_no, ln.quantity] for ln in lines),
    }))


def _find_refund_by_key(s: Session, tenant_id: str, key: str) -> Refund | None:
    return s.scalar(
        select(Refund).where(Refund.tenant_id == tenant_id, Refund.idempotency_key == key)
    )


def _refund_replay_or_conflict(existing: Refund, req_hash: str) -> tuple[Refund, bool]:
    if existing.request_hash != req_hash:
        raise IdempotencyConflict(
            "Idempotency-Key was already used for a different refund request"
        )
    return existing, True


def create_cash_refund(s: Session, **kw) -> tuple[Refund, bool]:
    """All-or-nothing wrapper: any domain error rolls back every counter
    already moved in this transaction (e.g. line 1 claimed, line 2 invalid)."""
    try:
        return _create_cash_refund(s, **kw)
    except DomainError:
        s.rollback()
        raise


def _create_cash_refund(
    s: Session,
    *,
    tenant_id: str,
    register_id: int,
    cashier_id: str,
    role: str,
    sale_id: str,
    idempotency_key: str,
    lines: list[RefundLineRequest],
    reason: str,
    settings: Settings,
    now: datetime,
) -> tuple[Refund, bool]:
    """Simulated cash refund against an existing sale. Returns ``(refund, replayed)``.

    Guarantees:
    - never refunds more units than were sold (per line, under concurrency);
    - the original sale, its lines and its outbox event are never modified;
    - the last unit of a line refunds exactly the remaining VAT, so a fully
      refunded line nets to zero VAT with no rounding drift;
    - cash is paid out of an *open* drawer that holds enough expected cash.
    """
    if role not in REFUND_ROLES:
        raise Forbidden("refunds require the manager or owner role")

    req_hash = _refund_request_hash(sale_id, register_id, lines, reason)
    existing = _find_refund_by_key(s, tenant_id, idempotency_key)
    if existing is not None:
        return _refund_replay_or_conflict(existing, req_hash)

    sale = get_sale(s, tenant_id, sale_id)  # 404 across tenants
    get_register(s, tenant_id, register_id)
    drawer = get_open_drawer(s, tenant_id, register_id)
    if drawer is None:
        raise NoOpenDrawer("open this register's cash drawer before refunding")

    seen = set()
    for ln in lines:
        if ln.line_no in seen:
            raise InvalidRefund(f"line {ln.line_no} listed twice")
        seen.add(ln.line_no)
    by_no = {ln.line_no: ln for ln in sale.lines}
    allocations = refund_state(s, tenant_id, sale_id)

    refund_id = str(uuid.uuid4())
    refund_lines: list[RefundLine] = []
    for i, req in enumerate(sorted(lines, key=lambda x: x.line_no), start=1):
        sl = by_no.get(req.line_no)
        if sl is None:
            raise InvalidRefund(f"sale has no line {req.line_no}")
        alloc = allocations.get(sl.id)
        if alloc is None:
            raise LedgerMismatch(f"no refund allocation for sale line {sl.id}")
        seen_qty = alloc.refunded_quantity
        remaining = alloc.original_quantity - seen_qty
        if req.quantity > remaining:
            raise OverRefund(
                f"line {req.line_no}: requested {req.quantity}, refundable {remaining}"
            )
        if req.quantity == remaining:
            # Last units: refund exactly what is left, so totals net to zero.
            gross = sl.gross_cents - alloc.refunded_gross_cents
            vat = sl.vat_cents - alloc.refunded_vat_cents
        else:
            gross = sl.unit_price_cents * req.quantity
            vat = vat_from_gross(gross, sl.vat_rate_bp)

        # Compare-and-swap on the counter: fails if anyone refunded this line
        # since we read it (stale read / concurrent refund). Never over-refunds.
        res = s.execute(
            update(RefundAllocation)
            .where(
                RefundAllocation.sale_line_id == sl.id,
                RefundAllocation.tenant_id == tenant_id,
                RefundAllocation.refunded_quantity == seen_qty,
                RefundAllocation.refunded_quantity + req.quantity
                <= RefundAllocation.original_quantity,
            )
            .values(
                refunded_quantity=RefundAllocation.refunded_quantity + req.quantity,
                refunded_gross_cents=RefundAllocation.refunded_gross_cents + gross,
                refunded_vat_cents=RefundAllocation.refunded_vat_cents + vat,
            )
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            s.rollback()
            raise OverRefund(
                f"line {req.line_no} was refunded concurrently; reload and retry"
            )
        refund_lines.append(RefundLine(
            refund_id=refund_id,
            tenant_id=tenant_id,
            line_no=i,
            sale_line_id=sl.id,
            sku=sl.sku,
            name=sl.name,
            unit_price_cents=sl.unit_price_cents,
            quantity=req.quantity,
            gross_cents=gross,
            vat_rate_bp=sl.vat_rate_bp,
            vat_cents=vat,
            net_cents=gross - vat,
        ))

    total = sum(x.gross_cents for x in refund_lines)
    vat_total = sum(x.vat_cents for x in refund_lines)
    cash_paid = round_cash(total, settings.cash_rounding_step_cents)

    # Pay out of the drawer only if it is still open and holds enough cash.
    res = s.execute(
        update(DrawerSession)
        .where(
            DrawerSession.id == drawer.id,
            DrawerSession.tenant_id == tenant_id,
            DrawerSession.status == "open",
            _expected_cash_expr() >= cash_paid,
        )
        .values(
            cash_refunds_cents=DrawerSession.cash_refunds_cents + cash_paid,
            refund_count=DrawerSession.refund_count + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        s.rollback()
        current = get_drawer(s, tenant_id, drawer.id)
        if current.status != "open":
            raise NoOpenDrawer("the cash drawer was closed; refund not recorded")
        raise InsufficientDrawerCash("not enough cash in this drawer for the refund")

    refund_no = (
        s.scalar(select(func.coalesce(func.max(Refund.refund_no), 0)).where(
            Refund.tenant_id == tenant_id
        ))
        + 1
    )
    refund = Refund(
        id=refund_id,
        tenant_id=tenant_id,
        refund_no=refund_no,
        original_sale_id=sale.id,
        register_id=register_id,
        drawer_session_id=drawer.id,
        cashier_id=cashier_id,
        approved_by=cashier_id,
        approver_role=role,
        reason=reason,
        idempotency_key=idempotency_key,
        request_hash=req_hash,
        correlation_id=str(uuid.uuid4()),
        created_at=now,
        business_date=business_date_for(now, settings),
        payment_method="cash",
        total_cents=total,
        vat_cents=vat_total,
        net_cents=total - vat_total,
        cash_rounding_cents=cash_paid - total,
        cash_paid_cents=cash_paid,
    )
    s.add(refund)
    s.add_all(refund_lines)
    s.add(_refund_completed_event(refund, refund_lines, sale))
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        existing = _find_refund_by_key(s, tenant_id, idempotency_key)
        if existing is not None:
            return _refund_replay_or_conflict(existing, req_hash)
        raise Conflict("concurrent refund conflict; retry with the same Idempotency-Key")
    s.refresh(refund)
    return refund, False


def _refund_completed_event(refund: Refund, lines: list[RefundLine], sale: Sale) -> OutboxEvent:
    payload = {
        "refund_id": refund.id,
        "refund_no": refund.refund_no,
        "original_sale_id": sale.id,
        "original_receipt_no": sale.receipt_no,
        "original_correlation_id": sale.correlation_id,
        "register_id": refund.register_id,
        "drawer_session_id": refund.drawer_session_id,
        "business_date": refund.business_date.isoformat(),
        "payment_method": refund.payment_method,
        "simulated": True,
        "total_cents": refund.total_cents,
        "vat_cents": refund.vat_cents,
        "net_cents": refund.net_cents,
        "cash_paid_cents": refund.cash_paid_cents,
        "cash_rounding_cents": refund.cash_rounding_cents,
        "approver_role": refund.approver_role,
        "lines": [
            {"sku": ln.sku, "quantity": ln.quantity, "gross_cents": ln.gross_cents,
             "vat_rate_bp": ln.vat_rate_bp, "vat_cents": ln.vat_cents}
            for ln in lines
        ],
    }
    body = _canonical(payload)
    return OutboxEvent(
        id=str(uuid.uuid4()),
        tenant_id=refund.tenant_id,
        type="pos.refund.completed",
        version=1,
        source="axar-pos",
        occurred_at=refund.created_at,
        correlation_id=refund.correlation_id,
        payload=body,
        payload_hash=_sha256(body),
    )


def get_refund(s: Session, tenant_id: str, refund_id: str) -> Refund:
    r = s.scalar(select(Refund).where(Refund.id == refund_id, Refund.tenant_id == tenant_id))
    if r is None:
        raise NotFound("refund not found")
    return r


def list_refunds(s: Session, tenant_id: str, *, sale_id: str | None = None,
                 business_date: date | None = None) -> list[Refund]:
    q = select(Refund).where(Refund.tenant_id == tenant_id)
    if sale_id is not None:
        q = q.where(Refund.original_sale_id == sale_id)
    if business_date is not None:
        q = q.where(Refund.business_date == business_date)
    return list(s.scalars(q.order_by(Refund.refund_no)))


# ------------------------------------------------------------------------- reports


def daily_summary(s: Session, tenant_id: str, business_date: date) -> dict:
    sales = list_sales(s, tenant_id, business_date)
    by_rate: dict[int, dict[str, int]] = {}
    by_register: dict[int, dict[str, int]] = {}
    for sale in sales:
        r = by_register.setdefault(sale.register_id, {"sale_count": 0, "total_cents": 0,
                                                      "cash_collected_cents": 0})
        r["sale_count"] += 1
        r["total_cents"] += sale.total_cents
        r["cash_collected_cents"] += sale.cash_due_cents
        for ln in sale.lines:
            b = by_rate.setdefault(ln.vat_rate_bp, {"gross_cents": 0, "vat_cents": 0, "net_cents": 0})
            b["gross_cents"] += ln.gross_cents
            b["vat_cents"] += ln.vat_cents
            b["net_cents"] += ln.net_cents

    refunds = list_refunds(s, tenant_id, business_date=business_date)
    refund_by_rate: dict[int, dict[str, int]] = {}
    for rf in refunds:
        r = by_register.setdefault(rf.register_id, {"sale_count": 0, "total_cents": 0,
                                                    "cash_collected_cents": 0})
        r["refund_count"] = r.get("refund_count", 0) + 1
        r["cash_refunded_cents"] = r.get("cash_refunded_cents", 0) + rf.cash_paid_cents
        for ln in rf.lines:
            b = refund_by_rate.setdefault(ln.vat_rate_bp,
                                          {"gross_cents": 0, "vat_cents": 0, "net_cents": 0})
            b["gross_cents"] += ln.gross_cents
            b["vat_cents"] += ln.vat_cents
            b["net_cents"] += ln.net_cents
    for r in by_register.values():
        r.setdefault("refund_count", 0)
        r.setdefault("cash_refunded_cents", 0)

    sales_total = sum(x.total_cents for x in sales)
    sales_vat = sum(x.vat_cents for x in sales)
    cash_in = sum(x.cash_due_cents for x in sales if x.payment_method == "cash")
    refund_total = sum(x.total_cents for x in refunds)
    refund_vat = sum(x.vat_cents for x in refunds)
    cash_out = sum(x.cash_paid_cents for x in refunds if x.payment_method == "cash")
    return {
        "refund_count": len(refunds),
        "refund_total_cents": refund_total,
        "refund_vat_cents": refund_vat,
        "refund_net_cents": refund_total - refund_vat,
        "refund_cash_rounding_cents": sum(x.cash_rounding_cents for x in refunds),
        "cash_refunded_cents": cash_out,
        "refund_vat_breakdown": [{"vat_rate_bp": r, **v} for r, v in sorted(refund_by_rate.items())],
        # Sales minus refunds recorded on this business day.
        "net_total_cents": sales_total - refund_total,
        "net_vat_cents": sales_vat - refund_vat,
        "net_cash_cents": cash_in - cash_out,
        "business_date": business_date.isoformat(),
        "sale_count": len(sales),
        "total_cents": sum(x.total_cents for x in sales),
        "net_cents": sum(x.net_cents for x in sales),
        "vat_cents": sum(x.vat_cents for x in sales),
        "cash_rounding_cents": sum(x.cash_rounding_cents for x in sales),
        "cash_collected_cents": sum(x.cash_due_cents for x in sales if x.payment_method == "cash"),
        "first_receipt_no": sales[0].receipt_no if sales else None,
        "last_receipt_no": sales[-1].receipt_no if sales else None,
        "vat_breakdown": [{"vat_rate_bp": r, **v} for r, v in sorted(by_rate.items())],
        "by_register": [{"register_id": r, **v} for r, v in sorted(by_register.items())],
    }


# ------------------------------------------------------------------------- receipt


def render_receipt_text(sale: Sale, tenant_name: str, settings: Settings) -> str:
    local = sale.created_at.astimezone(ZoneInfo(settings.business_timezone))
    width = 40
    out = [
        tenant_name.center(width),
        "SIMULEERITUD TŠEKK / DEMO".center(width),
        "-" * width,
        f"Tšekk nr {sale.receipt_no}",
        f"{sale.register.location.name} · {sale.register.name}"[:width],
        f"Aeg {local:%d.%m.%Y %H:%M}",
        f"Kassiir {sale.cashier_id}",
        "-" * width,
    ]
    for ln in sale.lines:
        out.append(ln.name[:width])
        qty = f"  {ln.quantity} x {format_eur(ln.unit_price_cents)}"
        out.append(f"{qty}{format_eur(ln.gross_cents):>{width - len(qty)}}")
    out.append("-" * width)

    def row(label: str, cents: int) -> str:
        return f"{label}{format_eur(cents):>{width - len(label)}}"

    out.append(row("KOKKU", sale.total_cents))
    rates: dict[int, list[int]] = {}
    for ln in sale.lines:
        r = rates.setdefault(ln.vat_rate_bp, [0, 0])
        r[0] += ln.net_cents
        r[1] += ln.vat_cents
    for bp, (net, vat) in sorted(rates.items()):
        pct = f"{bp // 100}%" if bp % 100 == 0 else f"{bp / 100:.2f}%"
        out.append(row(f"KM {pct} (neto {format_eur(net)})", vat))
    if sale.cash_rounding_cents:
        out.append(row("Ümardus", sale.cash_rounding_cents))
        out.append(row("Tasuda", sale.cash_due_cents))
    out.append(row("Sularaha", sale.tendered_cents))
    out.append(row("Tagasi", sale.change_cents))
    out.append("-" * width)
    out.append(f"ID {sale.id}")
    return "\n".join(out)


def _vat_rows(lines, row) -> list[str]:
    rates: dict[int, list[int]] = {}
    for ln in lines:
        r = rates.setdefault(ln.vat_rate_bp, [0, 0])
        r[0] += ln.net_cents
        r[1] += ln.vat_cents
    out = []
    for bp, (net, vat) in sorted(rates.items()):
        pct = f"{bp // 100}%" if bp % 100 == 0 else f"{bp / 100:.2f}%"
        out.append(row(f"KM {pct} (neto {format_eur(net)})", vat))
    return out


def render_refund_receipt_text(refund: Refund, tenant_name: str, settings: Settings) -> str:
    local = refund.created_at.astimezone(ZoneInfo(settings.business_timezone))
    width = 40

    def row(label: str, cents: int) -> str:
        return f"{label}{format_eur(cents):>{width - len(label)}}"

    out = [
        tenant_name.center(width),
        "TAGASTUS — SIMULEERITUD / DEMO".center(width),
        "-" * width,
        f"Tagastus nr {refund.refund_no}",
        f"Algne tšekk nr {refund.original_sale.receipt_no}",
        f"{refund.register.location.name} · {refund.register.name}"[:width],
        f"Aeg {local:%d.%m.%Y %H:%M}",
        f"Kinnitas {refund.approved_by} ({refund.approver_role})"[:width],
        f"Põhjus: {refund.reason}"[:width],
        "-" * width,
    ]
    for ln in refund.lines:
        out.append(ln.name[:width])
        qty = f"  -{ln.quantity} x {format_eur(ln.unit_price_cents)}"
        out.append(f"{qty}{format_eur(-ln.gross_cents):>{width - len(qty)}}")
    out.append("-" * width)
    out.append(row("TAGASTATUD KOKKU", refund.total_cents))
    out.extend(_vat_rows(refund.lines, row))
    if refund.cash_rounding_cents:
        out.append(row("Ümardus", refund.cash_rounding_cents))
    out.append(row("Sularahas välja", refund.cash_paid_cents))
    out.append("-" * width)
    out.append(f"ID {refund.id}")
    return "\n".join(out)
