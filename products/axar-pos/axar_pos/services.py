"""Domain services. All functions take an explicit ``tenant_id`` and every query
filters on it — there is no code path that reads business rows unscoped."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .models import DrawerSession, OutboxEvent, Product, Sale, SaleLine, TaxRate
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


def get_open_drawer(s: Session, tenant_id: str) -> DrawerSession | None:
    return s.scalar(
        select(DrawerSession).where(
            DrawerSession.tenant_id == tenant_id, DrawerSession.status == "open"
        )
    )


def open_drawer(
    s: Session, tenant_id: str, cashier_id: str, float_cents: int, now: datetime
) -> DrawerSession:
    if get_open_drawer(s, tenant_id) is not None:
        raise DrawerAlreadyOpen("a cash drawer session is already open")
    d = DrawerSession(
        tenant_id=tenant_id,
        opened_by=cashier_id,
        opened_at=now,
        opening_float_cents=float_cents,
        status="open",
    )
    s.add(d)
    try:
        s.commit()
    except IntegrityError:  # concurrent open lost the race
        s.rollback()
        raise DrawerAlreadyOpen("a cash drawer session is already open") from None
    return d


def drawer_cash_sales(s: Session, tenant_id: str, drawer_id: int) -> int:
    return int(
        s.scalar(
            select(func.coalesce(func.sum(Sale.cash_due_cents), 0)).where(
                Sale.tenant_id == tenant_id,
                Sale.drawer_session_id == drawer_id,
                Sale.payment_method == "cash",
            )
        )
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


def close_drawer(
    s: Session,
    tenant_id: str,
    drawer_id: int,
    cashier_id: str,
    counted_cents: int,
    note: str | None,
    now: datetime,
) -> DrawerSession:
    d = get_drawer(s, tenant_id, drawer_id)
    if d.status != "open":
        raise Conflict("drawer session is already closed")
    cash_sales = drawer_cash_sales(s, tenant_id, d.id)
    d.cash_sales_cents = cash_sales
    d.expected_cents = d.opening_float_cents + cash_sales
    d.counted_cents = counted_cents
    d.variance_cents = counted_cents - d.expected_cents
    d.closed_by = cashier_id
    d.closed_at = now
    d.note = note
    d.status = "closed"
    s.commit()
    return d


# --------------------------------------------------------------------------- sales


@dataclass
class LineRequest:
    product_id: int
    quantity: int


def _request_hash(lines: list[LineRequest], tendered_cents: int) -> str:
    return _sha256(
        _canonical(
            {
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
    cashier_id: str,
    idempotency_key: str,
    lines: list[LineRequest],
    tendered_cents: int,
    settings: Settings,
    now: datetime,
) -> tuple[Sale, bool]:
    """Create a cash sale. Returns ``(sale, replayed)``.

    A retry with the same key and identical body returns the original sale and
    creates nothing. The same key with a different body is a 409.
    """
    req_hash = _request_hash(lines, tendered_cents)

    existing = _find_by_key(s, tenant_id, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, req_hash)

    drawer = get_open_drawer(s, tenant_id)
    if drawer is None:
        raise NoOpenDrawer("open the cash drawer before selling")

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

    sale = Sale(
        id=sale_id,
        tenant_id=tenant_id,
        receipt_no=receipt_no,
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


# ------------------------------------------------------------------------- reports


def daily_summary(s: Session, tenant_id: str, business_date: date) -> dict:
    sales = list_sales(s, tenant_id, business_date)
    by_rate: dict[int, dict[str, int]] = {}
    for sale in sales:
        for ln in sale.lines:
            b = by_rate.setdefault(ln.vat_rate_bp, {"gross_cents": 0, "vat_cents": 0, "net_cents": 0})
            b["gross_cents"] += ln.gross_cents
            b["vat_cents"] += ln.vat_cents
            b["net_cents"] += ln.net_cents
    return {
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
