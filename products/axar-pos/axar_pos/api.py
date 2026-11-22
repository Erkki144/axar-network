# NOTE: no `from __future__ import annotations` here — FastAPI must resolve the
# locally defined dependency aliases (Who, Db) at route-definition time.
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from . import __version__, services
from .config import Settings
from .db import init_db, make_engine, seed_demo
from .models import DrawerSession, Sale, Tenant

STATIC = Path(__file__).parent / "static"

Cents = Annotated[int, Field(strict=True, ge=0, le=100_000_000)]
Qty = Annotated[int, Field(strict=True, ge=1, le=999)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SaleLineIn(Strict):
    product_id: Annotated[int, Field(strict=True, ge=1)]
    quantity: Qty


Id = Annotated[int, Field(strict=True, ge=1)]


class CashSaleIn(Strict):
    register_id: Id
    lines: Annotated[list[SaleLineIn], Field(min_length=1, max_length=200)]
    tendered_cents: Cents


class DrawerOpenIn(Strict):
    register_id: Id
    opening_float_cents: Cents


class DrawerCloseIn(Strict):
    counted_cents: Cents
    note: Annotated[str | None, Field(max_length=500)] = None


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    tenant_name: str
    cashier_id: str


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
        # While open: running expectation. Once closed: frozen at close.
        "expected_cents": d.expected_cents
        if d.status == "closed"
        else d.opening_float_cents + d.cash_sales_cents,
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
    ) -> Identity:
        if not settings.demo_identity:
            # Real authentication is issue #2. Refuse rather than guess.
            raise HTTPException(503, "identity provider not configured")
        if not x_tenant_id or not x_cashier_id:
            raise HTTPException(401, "X-Tenant-ID and X-Cashier-ID headers required (demo identity)")
        tenant = s.get(Tenant, x_tenant_id)
        if tenant is None:
            raise HTTPException(403, "unknown tenant")
        return Identity(tenant.id, tenant.name, x_cashier_id[:64])

    Db = Annotated[Session, Depends(db)]
    Who = Annotated[Identity, Depends(identity)]

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "payments": "simulated-cash-only"}

    @app.get("/", include_in_schema=False)
    def ui():
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/api/v1/products")
    def products(who: Who, s: Db):
        return [
            {"id": p.id, "sku": p.sku, "name": p.name,
             "price_cents": p.price_cents, "tax_category": p.tax_category}
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
        return _sale_out(services.get_sale(s, who.tenant_id, sale_id))

    @app.get("/api/v1/sales/{sale_id}/receipt.txt", response_class=PlainTextResponse)
    def receipt(sale_id: str, who: Who, s: Db):
        sale = services.get_sale(s, who.tenant_id, sale_id)
        return services.render_receipt_text(sale, who.tenant_name, settings)

    @app.get("/api/v1/reports/daily")
    def daily(who: Who, s: Db, business_date: date | None = None):
        bdate = business_date or services.business_date_for(settings.clock(), settings)
        return services.daily_summary(s, who.tenant_id, bdate)

    return app
