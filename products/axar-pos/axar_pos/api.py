# NOTE: no `from __future__ import annotations` here — FastAPI must resolve the
# locally defined dependency aliases (Who, Db) at route-definition time.
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
import mimetypes

from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

# ES modules must be served as JavaScript or browsers refuse to run them.
mimetypes.add_type("text/javascript", ".mjs")


class NoCacheStatic(StaticFiles):
    """Static files with no-store, so a cashier device never runs stale UI code."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

from . import __version__, orders, services
from .config import Settings
from .db import init_db, make_engine, seed_demo
from .models import DrawerSession, Refund, Sale, Tenant

STATIC = Path(__file__).parent / "static"

Cents = Annotated[int, Field(strict=True, ge=0, le=100_000_000)]
Qty = Annotated[int, Field(strict=True, ge=1, le=999)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SaleLineIn(Strict):
    product_id: Annotated[int, Field(strict=True, ge=1)]
    quantity: Qty


Id = Annotated[int, Field(strict=True, ge=1)]


Version = Annotated[int, Field(strict=True, ge=1)]


class OrderLineIn(Strict):
    product_id: Annotated[int, Field(strict=True, ge=1)]
    quantity: Annotated[int, Field(strict=True, ge=1, le=999)]


class OrderLinesIn(Strict):
    version: Version
    lines: Annotated[list[OrderLineIn], Field(max_length=200)]


class OrderVersionIn(Strict):
    version: Version


class PayOrderIn(Strict):
    version: Version
    register_id: Annotated[int, Field(strict=True, ge=1)]
    tendered_cents: Annotated[int, Field(strict=True, ge=0, le=100_000_000)]


class QuoteIn(Strict):
    lines: Annotated[list[SaleLineIn], Field(min_length=1, max_length=200)]


class CashSaleIn(Strict):
    register_id: Id
    lines: Annotated[list[SaleLineIn], Field(min_length=1, max_length=200)]
    tendered_cents: Cents


class DrawerOpenIn(Strict):
    register_id: Id
    opening_float_cents: Cents


class RefundLineIn(Strict):
    line_no: Id
    quantity: Qty


class CashRefundIn(Strict):
    register_id: Id
    lines: Annotated[list[RefundLineIn], Field(min_length=1, max_length=200)]
    reason: Annotated[str, Field(min_length=3, max_length=500)]


class DrawerCloseIn(Strict):
    counted_cents: Cents
    note: Annotated[str | None, Field(max_length=500)] = None


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    tenant_name: str
    cashier_id: str
    role: str


def _sale_out(sale: Sale) -> dict:
    return {
        "id": sale.id,
        "receipt_no": sale.receipt_no,
        "register_id": sale.register_id,
        "drawer_session_id": sale.drawer_session_id,
        "cashier_id": sale.cashier_id,
        "correlation_id": sale.correlation_id,
        "created_at": sale.created_at.isoformat(),
        "business_date": sale.business_date.isoformat(),
        "payment_method": sale.payment_method,
        "simulated": True,
        "total_cents": sale.total_cents,
        "net_cents": sale.net_cents,
        "vat_cents": sale.vat_cents,
        "cash_rounding_cents": sale.cash_rounding_cents,
        "cash_due_cents": sale.cash_due_cents,
        "tendered_cents": sale.tendered_cents,
        "change_cents": sale.change_cents,
        "lines": [
            {
                "line_no": ln.line_no,
                "product_id": ln.product_id,
                "sku": ln.sku,
                "name": ln.name,
                "unit_price_cents": ln.unit_price_cents,
                "quantity": ln.quantity,
                "gross_cents": ln.gross_cents,
                "vat_rate_bp": ln.vat_rate_bp,
                "vat_cents": ln.vat_cents,
                "net_cents": ln.net_cents,
            }
            for ln in sale.lines
        ],
    }


def _refund_out(r: Refund) -> dict:
    return {
        "id": r.id,
        "refund_no": r.refund_no,
        "original_sale_id": r.original_sale_id,
        "original_receipt_no": r.original_sale.receipt_no,
        "register_id": r.register_id,
        "drawer_session_id": r.drawer_session_id,
        "cashier_id": r.cashier_id,
        "approved_by": r.approved_by,
        "approver_role": r.approver_role,
        "reason": r.reason,
        "correlation_id": r.correlation_id,
        "created_at": r.created_at.isoformat(),
        "business_date": r.business_date.isoformat(),
        "payment_method": r.payment_method,
        "simulated": True,
        "total_cents": r.total_cents,
        "vat_cents": r.vat_cents,
        "net_cents": r.net_cents,
        "cash_rounding_cents": r.cash_rounding_cents,
        "cash_paid_cents": r.cash_paid_cents,
        "lines": [
            {"line_no": ln.line_no, "sale_line_id": ln.sale_line_id, "sku": ln.sku,
             "name": ln.name, "unit_price_cents": ln.unit_price_cents,
             "quantity": ln.quantity, "gross_cents": ln.gross_cents,
             "vat_rate_bp": ln.vat_rate_bp, "vat_cents": ln.vat_cents, "net_cents": ln.net_cents}
            for ln in r.lines
        ],
    }


def _drawer_out(d: DrawerSession) -> dict:
    return {
        "id": d.id,
        "register_id": d.register_id,
        "status": d.status,
        "opened_by": d.opened_by,
        "opened_at": d.opened_at.isoformat(),
        "opening_float_cents": d.opening_float_cents,
        "closed_by": d.closed_by,
        "closed_at": d.closed_at.isoformat() if d.closed_at else None,
        "sale_count": d.sale_count,
        "cash_sales_cents": d.cash_sales_cents,
        "refund_count": d.refund_count,
        "cash_refunds_cents": d.cash_refunds_cents,
        # While open: running expectation. Once closed: frozen at close.
        "expected_cents": d.expected_cents
        if d.status == "closed"
        else d.opening_float_cents + d.cash_sales_cents - d.cash_refunds_cents,
        "counted_cents": d.counted_cents,
        "variance_cents": d.variance_cents,
        "note": d.note,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    factory: sessionmaker[Session] = init_db(engine)
    if settings.seed_demo_data:
        seed_demo(factory)

    app = FastAPI(title="AXAR POS (Slice 1, simulated cash)", version=__version__)
    app.state.settings = settings
    app.state.session_factory = factory

    @app.exception_handler(services.DomainError)
    async def _domain_error(_req: Request, exc: services.DomainError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message},
        )

    def db() -> Iterator[Session]:
        s = factory()
        try:
            yield s
        finally:
            s.close()

    def identity(
        s: Annotated[Session, Depends(db)],
        x_tenant_id: Annotated[str | None, Header()] = None,
        x_cashier_id: Annotated[str | None, Header()] = None,
        x_role: Annotated[str | None, Header()] = None,
    ) -> Identity:
        if not settings.demo_identity:
            # Real authentication is issue #2. Refuse rather than guess.
            raise HTTPException(503, "identity provider not configured")
        if not x_tenant_id or not x_cashier_id:
            raise HTTPException(401, "X-Tenant-ID and X-Cashier-ID headers required (demo identity)")
        tenant = s.get(Tenant, x_tenant_id)
        if tenant is None:
            raise HTTPException(403, "unknown tenant")
        role = x_role or "cashier"
        if role not in services.ROLES:
            raise HTTPException(403, "invalid role")
        return Identity(tenant.id, tenant.name, x_cashier_id[:64], role)

    Db = Annotated[Session, Depends(db)]
    Who = Annotated[Identity, Depends(identity)]

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "payments": "simulated-cash-only"}

    @app.get("/", include_in_schema=False)
    def ui_root():
        return RedirectResponse("/one/", status_code=307)

    @app.get("/classic", include_in_schema=False)
    def ui_classic():
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")

    # AXAR ONE cashier UI (issue #11), served same-origin so it can call the API.
    app.mount("/one", NoCacheStatic(directory=STATIC / "one", html=True), name="one")

    @app.get("/api/v1/products")
    def products(who: Who, s: Db):
        return [
            {"id": p.id, "sku": p.sku, "name": p.name,
             "price_cents": p.price_cents, "tax_category": p.tax_category,
             "category": p.category}
            for p in services.list_products(s, who.tenant_id)
        ]

    @app.get("/api/v1/registers")
    def registers(who: Who, s: Db):
        out = []
        for r in services.list_registers(s, who.tenant_id):
            d = services.get_open_drawer(s, who.tenant_id, r.id)
            out.append({
                "id": r.id, "code": r.code, "name": r.name,
                "location": {"id": r.location.id, "code": r.location.code, "name": r.location.name},
                "open_drawer": _drawer_out(d) if d else None,
            })
        return out

    @app.get("/api/v1/registers/{register_id}/drawer")
    def drawer_current(register_id: int, who: Who, s: Db):
        services.get_register(s, who.tenant_id, register_id)
        d = services.get_open_drawer(s, who.tenant_id, register_id)
        return {"drawer": _drawer_out(d) if d else None}

    @app.get("/api/v1/drawer/{drawer_id}")
    def drawer_get(drawer_id: int, who: Who, s: Db):
        return _drawer_out(services.get_drawer(s, who.tenant_id, drawer_id))

    @app.post("/api/v1/drawer/open", status_code=201)
    def drawer_open(body: DrawerOpenIn, who: Who, s: Db):
        d = services.open_drawer(s, who.tenant_id, body.register_id, who.cashier_id,
                                 body.opening_float_cents, settings.clock())
        return _drawer_out(d)

    @app.post("/api/v1/drawer/{drawer_id}/close")
    def drawer_close(drawer_id: int, body: DrawerCloseIn, who: Who, s: Db):
        d = services.close_drawer(s, who.tenant_id, drawer_id, who.cashier_id,
                                  body.counted_cents, body.note, settings.clock())
        return _drawer_out(d)

    @app.post("/api/v1/sales/quote")
    def quote_sale(body: QuoteIn, who: Who, s: Db):
        """Non-binding server price preview for the basket. Writes nothing."""
        return services.quote_cash_sale(
            s,
            tenant_id=who.tenant_id,
            lines=[services.LineRequest(ln.product_id, ln.quantity) for ln in body.lines],
            settings=settings,
            now=settings.clock(),
        )

    @app.post("/api/v1/sales/cash")
    def create_cash_sale(
        body: CashSaleIn,
        who: Who,
        s: Db,
        idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
    ):
        sale, replayed = services.create_cash_sale(
            s,
            tenant_id=who.tenant_id,
            register_id=body.register_id,
            cashier_id=who.cashier_id,
            idempotency_key=idempotency_key,
            lines=[services.LineRequest(ln.product_id, ln.quantity) for ln in body.lines],
            tendered_cents=body.tendered_cents,
            settings=settings,
            now=settings.clock(),
        )
        return JSONResponse(
            status_code=200 if replayed else 201,
            content=_sale_out(sale),
            headers={"Idempotent-Replayed": "true" if replayed else "false"},
        )

    @app.get("/api/v1/sales")
    def sales(who: Who, s: Db, business_date: date | None = None):
        return [_sale_out(x) for x in services.list_sales(s, who.tenant_id, business_date)]

    @app.get("/api/v1/sales/{sale_id}")
    def sale(sale_id: str, who: Who, s: Db):
        sale = services.get_sale(s, who.tenant_id, sale_id)
        out = _sale_out(sale)
        state = services.refund_state(s, who.tenant_id, sale.id)
        for ln_out, ln in zip(out["lines"], sale.lines):
            alloc = state.get(ln.id)
            ln_out["refunded_quantity"] = alloc.refunded_quantity if alloc else 0
        return out

    @app.post("/api/v1/sales/{sale_id}/refunds")
    def create_refund(
        sale_id: str,
        body: CashRefundIn,
        who: Who,
        s: Db,
        idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
    ):
        refund, replayed = services.create_cash_refund(
            s,
            tenant_id=who.tenant_id,
            register_id=body.register_id,
            cashier_id=who.cashier_id,
            role=who.role,
            sale_id=sale_id,
            idempotency_key=idempotency_key,
            lines=[services.RefundLineRequest(x.line_no, x.quantity) for x in body.lines],
            reason=body.reason,
            settings=settings,
            now=settings.clock(),
        )
        return JSONResponse(
            status_code=200 if replayed else 201,
            content=_refund_out(refund),
            headers={"Idempotent-Replayed": "true" if replayed else "false"},
        )

    @app.get("/api/v1/sales/{sale_id}/refunds")
    def sale_refunds(sale_id: str, who: Who, s: Db):
        services.get_sale(s, who.tenant_id, sale_id)
        return [_refund_out(r) for r in services.list_refunds(s, who.tenant_id, sale_id=sale_id)]

    @app.get("/api/v1/refunds/{refund_id}")
    def refund_get(refund_id: str, who: Who, s: Db):
        return _refund_out(services.get_refund(s, who.tenant_id, refund_id))

    @app.get("/api/v1/refunds/{refund_id}/receipt.txt", response_class=PlainTextResponse)
    def refund_receipt(refund_id: str, who: Who, s: Db):
        refund = services.get_refund(s, who.tenant_id, refund_id)
        return services.render_refund_receipt_text(refund, who.tenant_name, settings)

    @app.get("/api/v1/sales/{sale_id}/receipt.txt", response_class=PlainTextResponse)
    def receipt(sale_id: str, who: Who, s: Db):
        sale = services.get_sale(s, who.tenant_id, sale_id)
        return services.render_receipt_text(sale, who.tenant_name, settings)

    @app.get("/api/v1/reports/daily")
    def daily(who: Who, s: Db, business_date: date | None = None):
        bdate = business_date or services.business_date_for(settings.clock(), settings)
        return services.daily_summary(s, who.tenant_id, bdate)

    # ------------------------------------------------- tables / orders / kitchen

    def order_out(o) -> dict:
        # Product names/prices are resolved client-side from the catalog;
        # amounts come from the server quote and the sale.
        return {
            "id": o.id, "table_id": o.table_id, "table_name": o.table.name, "status": o.status,
            "version": o.version, "opened_by": o.opened_by, "sale_id": o.sale_id,
            "created_at": o.created_at.isoformat(), "updated_at": o.updated_at.isoformat(),
            "lines": [{"product_id": ln.product_id, "quantity": ln.quantity, "sent_quantity": ln.sent_quantity}
                      for ln in o.lines],
        }

    def ticket_out(t) -> dict:
        return {"id": t.id, "ticket_no": t.ticket_no, "order_id": t.order_id, "table_name": t.table_name,
                "status": t.status, "created_at": t.created_at.isoformat(),
                "ready_at": t.ready_at.isoformat() if t.ready_at else None,
                "lines": [{"product_id": ln.product_id, "name": ln.name, "quantity": ln.quantity} for ln in t.lines]}

    @app.get("/api/v1/tables")
    def tables(who: Who, s: Db):
        out = []
        for t in orders.list_tables(s, who.tenant_id):
            o = orders.open_order_for_table(s, who.tenant_id, t.id)
            out.append({"id": t.id, "code": t.code, "name": t.name, "location_id": t.location_id,
                        "open_order": None if o is None else {
                            "id": o.id, "version": o.version,
                            "item_count": sum(ln.quantity for ln in o.lines)}})
        return out

    @app.post("/api/v1/tables/{table_id}/order")
    def table_order(table_id: int, who: Who, s: Db):
        """Get-or-create the table's open order (idempotent by design)."""
        return order_out(orders.open_order(s, who.tenant_id, table_id, who.cashier_id, settings.clock()))

    @app.get("/api/v1/orders/{order_id}")
    def order_get(order_id: str, who: Who, s: Db):
        return order_out(orders.get_order(s, who.tenant_id, order_id))

    @app.put("/api/v1/orders/{order_id}/lines")
    def order_lines(order_id: str, body: OrderLinesIn, who: Who, s: Db):
        o = orders.set_lines(s, who.tenant_id, order_id, body.version,
                             [(x.product_id, x.quantity) for x in body.lines], settings.clock())
        return order_out(o)

    @app.post("/api/v1/orders/{order_id}/kitchen")
    def order_kitchen(order_id: str, body: OrderVersionIn, who: Who, s: Db,
                      idempotency_key: Annotated[str, Header(min_length=8, max_length=128)]):
        ticket, replayed = orders.send_to_kitchen(s, who.tenant_id, order_id, body.version,
                                                  idempotency_key, settings.clock())
        return JSONResponse(status_code=200 if replayed else 201, content={
            "ticket": ticket_out(ticket), "order": order_out(orders.get_order(s, who.tenant_id, order_id))},
            headers={"Idempotent-Replayed": "true" if replayed else "false"})

    @app.post("/api/v1/orders/{order_id}/pay-cash")
    def order_pay(order_id: str, body: PayOrderIn, who: Who, s: Db,
                  idempotency_key: Annotated[str, Header(min_length=8, max_length=128)]):
        sale, replayed = orders.pay_order_cash(
            s, tenant_id=who.tenant_id, order_id=order_id, expected_version=body.version,
            register_id=body.register_id, cashier_id=who.cashier_id, tendered_cents=body.tendered_cents,
            idempotency_key=idempotency_key, settings=settings, now=settings.clock())
        return JSONResponse(status_code=200 if replayed else 201, content=_sale_out(sale),
                            headers={"Idempotent-Replayed": "true" if replayed else "false"})

    @app.get("/api/v1/kitchen/tickets")
    def kitchen_tickets(who: Who, s: Db):
        return [ticket_out(t) for t in orders.list_tickets(s, who.tenant_id)]

    @app.post("/api/v1/kitchen/tickets/{ticket_id}/ready")
    def kitchen_ready(ticket_id: str, who: Who, s: Db):
        return ticket_out(orders.mark_ready(s, who.tenant_id, ticket_id, settings.clock()))

    @app.get("/api/v1/operations/{idempotency_key}")
    def operation(idempotency_key: str, who: Who, s: Db):
        """Was a sale/refund with this key recorded? (tenant-scoped)"""
        return orders.operation_status(s, who.tenant_id, idempotency_key)

    return app
