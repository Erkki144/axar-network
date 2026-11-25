# NOTE: no `from __future__ import annotations` here — FastAPI must resolve the
# locally defined dependency aliases (Who, Db) at route-definition time.
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Iterator, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
import mimetypes

from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import asyncio
import json
import logging
import time
import uuid

from starlette.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

log = logging.getLogger("axar.api")

# ES modules must be served as JavaScript or browsers refuse to run them.
mimetypes.add_type("text/javascript", ".mjs")


class NoCacheStatic(StaticFiles):
    """Static files with no-store, so a cashier device never runs stale UI code."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

from . import __version__, auth, events, inventory, offline, orders, payments, services, sync
from .config import Settings
from .db import bind_tenant, init_db, make_engine, request_factory, seed_demo
from .models import DrawerSession, Refund, Register, Sale, Tenant

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


class BillPaymentIn(Strict):
    """One tender against an open bill (C3). ``expected_total_cents`` is the
    total the device showed; a changed bill is rejected (409 bill_changed)."""
    method: Literal["cash", "card", "transfer"]
    register_id: Annotated[int, Field(strict=True, ge=1)]
    expected_total_cents: Cents
    amount_cents: Annotated[int, Field(strict=True, ge=1, le=100_000_000)]
    tendered_cents: Cents = 0                                   # cash
    simulate: Literal["approved", "declined", "timeout"] = "approved"  # card (MOCK terminal)
    customer_name: Annotated[str, Field(max_length=200)] = ""   # transfer
    customer_code: Annotated[str | None, Field(max_length=32)] = None
    due_days: Annotated[int, Field(strict=True, ge=0, le=90)] = 14


class BankLineIn(Strict):
    bank_tx_id: Annotated[str, Field(min_length=1, max_length=64)]
    booked_date: date
    amount_cents: Annotated[int, Field(strict=True, ge=-100_000_000, le=100_000_000)]
    reference: Annotated[str, Field(max_length=35)] = ""
    payer: Annotated[str, Field(max_length=200)] = ""


class BankImportIn(Strict):
    lines: Annotated[list[BankLineIn], Field(min_length=1, max_length=1000)]


class QuoteIn(Strict):
    lines: Annotated[list[SaleLineIn], Field(min_length=1, max_length=200)]


class CashSaleIn(Strict):
    register_id: Id
    lines: Annotated[list[SaleLineIn], Field(min_length=1, max_length=200)]
    tendered_cents: Cents


class SyncEntryIn(Strict):
    entry: dict
    payload: Annotated[str, Field(max_length=20_000)]
    payload_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SyncPushIn(Strict):
    device_id: Annotated[str, Field(min_length=1, max_length=64)]
    # empty = heartbeat: confirms contact and fetches the current policy
    entries: Annotated[list[SyncEntryIn], Field(min_length=0, max_length=500)]


class PosSettingsIn(Strict):
    """POS settings a manager edits (owner decision 2026-11-25: limits are set
    in the POS settings screen). Offline limit: 1-72 h."""
    offline_limit_hours: Annotated[int, Field(ge=1, le=offline.MAX_OFFLINE_HOURS)]


class DismissIn(Strict):
    note: Annotated[str, Field(min_length=5, max_length=300)]


class ReceiptSeriesIn(Strict):
    """Per-register receipt series (owner decision 2026-11-25: set in settings).
    ``prefix`` null = labels off. ``next_number`` can only move forward."""
    prefix: Annotated[str | None, Field(pattern=r"^[A-Z0-9]{1,8}$")] = None
    next_number: Annotated[int, Field(strict=True, ge=1, le=99_999_999)]


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


Identity = auth.Principal  # backwards-compatible name


class EnrollIn(Strict):
    code: Annotated[str, Field(min_length=8, max_length=40)]


class PinLoginIn(Strict):
    staff_id: Annotated[str, Field(min_length=1, max_length=36)]
    pin: Annotated[str, Field(min_length=1, max_length=16)]


class PasswordLoginIn(Strict):
    tenant_id: Annotated[str, Field(min_length=1, max_length=64)]
    login: Annotated[str, Field(min_length=1, max_length=64)]
    password: Annotated[str, Field(min_length=1, max_length=200)]
    totp: Annotated[str, Field(min_length=6, max_length=6)]


class RefundApprovalRequest(Strict):
    sale_id: Annotated[str, Field(min_length=1, max_length=36)]
    register_id: Id
    lines: Annotated[list[RefundLineIn], Field(min_length=1, max_length=200)]
    reason: Annotated[str, Field(min_length=3, max_length=500)]


class DrawerCloseApprovalRequest(Strict):
    drawer_id: Id
    counted_cents: Cents


class ApprovalIn(Strict):
    action: Literal["refund", "drawer_close"]
    approver_id: Annotated[str, Field(min_length=1, max_length=36)]
    pin: Annotated[str, Field(min_length=1, max_length=16)]
    request: dict


class RoleIn(Strict):
    role: Literal["cashier", "waiter", "kitchen", "manager", "owner"]
    location_id: Annotated[int | None, Field(strict=True, ge=1)] = None


class StaffIn(Strict):
    display_name: Annotated[str, Field(min_length=1, max_length=100)]
    roles: Annotated[list[RoleIn], Field(min_length=1, max_length=20)]
    pin: Annotated[str | None, Field(min_length=4, max_length=8)] = None


class PinIn(Strict):
    pin: Annotated[str, Field(min_length=4, max_length=8)]


class CredentialsIn(Strict):
    login: Annotated[str, Field(min_length=3, max_length=64, pattern=r"^[a-z0-9._-]+$")]
    password: Annotated[str, Field(min_length=12, max_length=200)]


class EnrollCodeIn(Strict):
    location_id: Id
    register_id: Annotated[int | None, Field(strict=True, ge=1)] = None
    device_name: Annotated[str, Field(min_length=1, max_length=100)]


class ItemIn(Strict):
    sku: Annotated[str, Field(min_length=1, max_length=64)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    kind: Literal["ingredient", "finished", "resale", "packaging"]
    uom: Literal["kg", "l", "pcs"]


class WarehouseIn(Strict):
    location_id: Id
    code: Annotated[str, Field(min_length=1, max_length=32)]
    name: Annotated[str, Field(min_length=1, max_length=100)]
    is_default: bool = False


class SupplierIn(Strict):
    code: Annotated[str, Field(min_length=1, max_length=32)]
    name: Annotated[str, Field(min_length=1, max_length=200)]


MicroQty = Annotated[int, Field(strict=True, ge=1, le=10**15)]


class DocLineIn(Strict):
    item_id: Id
    qty_micro: MicroQty
    net_cents: Cents
    vat_rate_bp: Annotated[int, Field(strict=True, ge=0, le=10000)]
    vat_recoverable: bool = True


class SupplierDocIn(Strict):
    supplier_id: Id
    doc_type: Literal["delivery_note", "invoice"]
    doc_no: Annotated[str, Field(min_length=1, max_length=64)]
    doc_date: date
    warehouse_id: Id
    lines: Annotated[list[DocLineIn], Field(min_length=1, max_length=500)]
    freight_cents: Cents = 0
    matches_document_id: Annotated[str | None, Field(max_length=36)] = None


class ReturnIn(Strict):
    item_id: Id
    qty_micro: MicroQty
    return_no: Annotated[str, Field(min_length=1, max_length=64)]


class RecipeLineIn(Strict):
    item_id: Id
    qty_micro: MicroQty


class RecipeIn(Strict):
    product_id: Annotated[int | None, Field(strict=True, ge=1)] = None
    output_item_id: Annotated[int | None, Field(strict=True, ge=1)] = None
    lines: Annotated[list[RecipeLineIn], Field(min_length=1, max_length=100)]
    yield_micro: MicroQty = 1_000_000
    valid_from: date


class SellItemIn(Strict):
    product_id: Id
    item_id: Id


class ProduceIn(Strict):
    output_item_id: Id
    warehouse_id: Id
    batches: Annotated[int, Field(strict=True, ge=1, le=10_000)]


class RecipePreviewIn(Strict):
    product_id: Annotated[int | None, Field(strict=True, ge=1)] = None
    lines: Annotated[list[RecipeLineIn], Field(min_length=0, max_length=100)]
    yield_micro: MicroQty = 1_000_000


def drawer_close_hash(drawer_id: int, counted_cents: int) -> str:
    return services._sha256(services._canonical({"drawer_id": drawer_id, "counted_cents": counted_cents}))


def _sale_out(sale: Sale) -> dict:
    return {
        "id": sale.id,
        "receipt_no": sale.receipt_no,
        "register_receipt_no": sale.register_receipt_no,
        "receipt_label": sale.receipt_label,
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
    # Fail closed BEFORE touching any database.
    if settings.profile not in ("dev", "pilot"):
        raise RuntimeError(f"unknown AXAR_POS_PROFILE {settings.profile!r}")
    if settings.profile == "pilot" and settings.pilot_problems():
        raise RuntimeError("refusing to start the pilot profile: " + "; ".join(settings.pilot_problems()))
    admin_engine = make_engine(settings.admin_database_url or settings.database_url)
    admin_factory: sessionmaker[Session] = init_db(admin_engine)
    if settings.seed_demo_data:
        seed_demo(admin_factory)
    engine = make_engine(settings.database_url) if settings.admin_database_url else admin_engine
    factory = request_factory(engine)

    if settings.demo_identity and settings.secret_key:
        # Fail closed: header identity must never coexist with real auth.
        raise RuntimeError("AXAR_POS_DEMO_IDENTITY cannot be enabled when AXAR_POS_SECRET_KEY is set")
    box = auth.SecretBox(settings.secret_key) if settings.secret_key else None
    login_limiter = auth.RateLimiter(limit=20, window_s=300)

    lifespan = None
    if settings.inprocess_worker:
        import contextlib
        import threading

        from . import worker as _worker

        @contextlib.asynccontextmanager
        async def lifespan(app_: FastAPI):
            # Lifespan (Starlette >= 1.0 removed add_event_handler/on_event).
            stop = threading.Event()

            def loop():
                wid = f"inproc:{uuid.uuid4().hex[:8]}"
                while not stop.wait(1.0):
                    try:
                        _worker.run_once(app_.state.request_factory, settings, wid)
                    except Exception:  # never kill the API; failures are recorded per consumer
                        log.exception("in-process worker round failed")

            t = threading.Thread(target=loop, daemon=True, name="axar-worker")
            t.start()
            try:
                yield
            finally:
                stop.set()
                t.join(timeout=5)

    app = FastAPI(title="AXAR POS (simulated cash)", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    # session_factory: owner/maintenance sessions (seeding, tests, admin CLI);
    # NOT subject to RLS. request_factory: what API requests use.
    app.state.session_factory = admin_factory
    app.state.request_factory = factory
    app.state.engine = engine
    app.state.admin_engine = admin_engine

    @app.exception_handler(services.DomainError)
    async def _domain_error(_req: Request, exc: services.DomainError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message},
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Request ID (trace) + one structured log line per request. No bodies,
        tokens or PINs are logged."""
        rid = request.headers.get("x-request-id", "")
        if not (8 <= len(rid) <= 64 and all(ch.isalnum() or ch in "-_" for ch in rid)):
            rid = str(uuid.uuid4())
        request.state.request_id = rid
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            route = request.scope.get("route")
            log.info(json.dumps({"event": "http", "request_id": rid, "method": request.method,
                                 "route": getattr(route, "path", request.url.path), "status": status,
                                 "ms": round((time.perf_counter() - start) * 1000, 1)}))

    def db(request: Request) -> Iterator[Session]:
        s = factory()
        s.info["correlation_id"] = getattr(request.state, "request_id", None)
        try:
            yield s
        finally:
            s.close()

    def identity(
        request: Request,
        s: Annotated[Session, Depends(db)],
        authorization: Annotated[str | None, Header()] = None,
        x_device_token: Annotated[str | None, Header()] = None,
        x_tenant_id: Annotated[str | None, Header()] = None,
        x_cashier_id: Annotated[str | None, Header()] = None,
        x_role: Annotated[str | None, Header()] = None,
    ) -> auth.Principal:
        if not settings.demo_identity:
            # Server-side sessions only. X-Tenant-ID / X-Cashier-ID / X-Role
            # are ignored entirely here (RFC-0002 threat: role spoofing).
            bearer = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
            who = auth.resolve_session(s, bearer=bearer, device_token=x_device_token,
                                       settings=settings, now=settings.clock(),
                                       touch=request.url.path != "/api/v1/events/stream")
            s.info["actor"] = {"kind": "staff", "staff_id": who.staff_id, "device_id": who.device_id,
                               "session_id": who.session_id}
            return who
        if not x_tenant_id or not x_cashier_id:
            raise HTTPException(401, "X-Tenant-ID and X-Cashier-ID headers required (demo identity)")
        tenant = s.get(Tenant, x_tenant_id)
        if tenant is None:
            raise HTTPException(403, "unknown tenant")
        role = x_role or "cashier"
        if role not in services.ROLES:
            raise HTTPException(403, "invalid role")
        bind_tenant(s, tenant.id)
        s.info["actor"] = {"kind": "staff", "staff_id": x_cashier_id[:64], "device_id": None, "session_id": None}
        return auth.Principal(tenant_id=tenant.id, tenant_name=tenant.name, staff_id=x_cashier_id[:64],
                              staff_name=x_cashier_id[:64], roles=frozenset({role}), demo=True)

    def need(permission: str):
        def dep(who: Annotated[auth.Principal, Depends(identity)]) -> auth.Principal:
            if not who.can(permission):
                raise services.Forbidden(f"your role may not do this ({permission})")
            return who
        return dep

    def till_register(who: auth.Principal, register_id: int) -> None:
        """Money and drawer actions: only on the register bound to this device."""
        if who.demo:
            return
        if who.device_id is None:
            raise auth.DeviceRequired("this action must be done on an enrolled till")
        if who.register_id != register_id:
            raise services.Forbidden("this device is bound to another register")

    def same_location(who: auth.Principal, location_id: int | None) -> None:
        """Till sessions see only their location's tables, bills and tickets."""
        if who.demo or who.location_id is None:
            return
        if location_id != who.location_id:
            raise services.NotFound("not found at this location")

    def client_ip(request: Request) -> str | None:
        return request.client.host if request.client else None

    Db = Annotated[Session, Depends(db)]
    Who = Annotated[auth.Principal, Depends(identity)]
    Catalog = Annotated[auth.Principal, Depends(need("catalog.read"))]
    Seller = Annotated[auth.Principal, Depends(need("sale"))]
    SalesReader = Annotated[auth.Principal, Depends(need("sales.read"))]
    DrawerUser = Annotated[auth.Principal, Depends(need("drawer"))]
    Refunder = Annotated[auth.Principal, Depends(need("refund.request"))]
    Reporter = Annotated[auth.Principal, Depends(need("reports"))]
    Waiter = Annotated[auth.Principal, Depends(need("orders"))]
    KitchenReader = Annotated[auth.Principal, Depends(need("kitchen.read"))]
    KitchenUser = Annotated[auth.Principal, Depends(need("kitchen.update"))]

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
    def products(who: Catalog, s: Db):
        return [
            {"id": p.id, "sku": p.sku, "name": p.name,
             "price_cents": p.price_cents, "tax_category": p.tax_category,
             "category": p.category}
            for p in services.list_products(s, who.tenant_id)
        ]

    @app.get("/api/v1/registers")
    def registers(who: Catalog, s: Db):
        out = []
        for r in services.list_registers(s, who.tenant_id):
            if not who.demo and who.register_id is not None and r.id != who.register_id:
                continue  # a till sees only its own register
            d = services.get_open_drawer(s, who.tenant_id, r.id)
            out.append({
                "id": r.id, "code": r.code, "name": r.name,
                "location": {"id": r.location.id, "code": r.location.code, "name": r.location.name},
                "open_drawer": _drawer_out(d) if d else None,
            })
        return out

    @app.get("/api/v1/registers/{register_id}/drawer")
    def drawer_current(register_id: int, who: Catalog, s: Db):
        services.get_register(s, who.tenant_id, register_id)
        d = services.get_open_drawer(s, who.tenant_id, register_id)
        return {"drawer": _drawer_out(d) if d else None}

    @app.get("/api/v1/drawer/{drawer_id}")
    def drawer_get(drawer_id: int, who: DrawerUser, s: Db):
        return _drawer_out(services.get_drawer(s, who.tenant_id, drawer_id))

    @app.post("/api/v1/drawer/open", status_code=201)
    def drawer_open(body: DrawerOpenIn, who: DrawerUser, s: Db):
        till_register(who, body.register_id)
        d = services.open_drawer(s, who.tenant_id, body.register_id, who.cashier_id,
                                 body.opening_float_cents, settings.clock())
        return _drawer_out(d)

    @app.post("/api/v1/drawer/{drawer_id}/close")
    def drawer_close(drawer_id: int, body: DrawerCloseIn, who: DrawerUser, s: Db,
                     x_approval_id: Annotated[str | None, Header(max_length=36)] = None):
        d0 = services.get_drawer(s, who.tenant_id, drawer_id)
        till_register(who, d0.register_id)
        now = settings.clock()

        def variance_allowed(sess, _d) -> bool:
            # Managers may close with a variance; others need a step-up approval
            # bound to exactly this drawer and counted amount.
            if who.demo or who.roles & set(auth.MANAGER_ROLES):
                return True
            if not x_approval_id:
                return False
            a = auth.peek_approval(sess, who, x_approval_id, "drawer_close",
                                   drawer_close_hash(drawer_id, body.counted_cents), now)
            auth.consume_approval(sess, a.id, now)
            return True

        d = services.close_drawer(s, who.tenant_id, drawer_id, who.cashier_id,
                                  body.counted_cents, body.note, now, variance_allowed=variance_allowed)
        return _drawer_out(d)

    @app.post("/api/v1/sales/quote")
    def quote_sale(body: QuoteIn, who: Catalog, s: Db):
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
        who: Seller,
        s: Db,
        idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
    ):
        till_register(who, body.register_id)
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
            actor_device_id=who.device_id,
            actor_session_id=who.session_id,
        )
        return JSONResponse(
            status_code=200 if replayed else 201,
            content=_sale_out(sale),
            headers={"Idempotent-Replayed": "true" if replayed else "false"},
        )

    @app.get("/api/v1/sales")
    def sales(who: SalesReader, s: Db, business_date: date | None = None):
        return [_sale_out(x) for x in services.list_sales(s, who.tenant_id, business_date)
                if who.location_id is None or x.register.location_id == who.location_id]

    def visible_sale(s: Session, who: auth.Principal, sale_id: str):
        sale = services.get_sale(s, who.tenant_id, sale_id)
        same_location(who, sale.register.location_id)
        return sale

    @app.get("/api/v1/sales/{sale_id}")
    def sale(sale_id: str, who: SalesReader, s: Db):
        sale = visible_sale(s, who, sale_id)
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
        who: Refunder,
        s: Db,
        idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
        x_approval_id: Annotated[str | None, Header(max_length=36)] = None,
    ):
        till_register(who, body.register_id)
        if not who.demo:
            visible_sale(s, who, sale_id)
        now = settings.clock()
        lines = [services.RefundLineRequest(x.line_no, x.quantity) for x in body.lines]
        role, approved_by, before_commit = who.role, None, None
        if not who.demo:
            existing = services._find_refund_by_key(s, who.tenant_id, idempotency_key)
            if existing is not None:   # replay: approval was consumed by the original
                role, approved_by = existing.approver_role, existing.approved_by
            else:
                req_hash = services._refund_request_hash(sale_id, body.register_id, lines, body.reason)
                a = auth.peek_approval(s, who, x_approval_id, "refund", req_hash, now)
                role, approved_by = a.approver_role, a.approver_id

                def before_commit(sess, _refund, approval_id=a.id):
                    auth.consume_approval(sess, approval_id, now)
        refund, replayed = services.create_cash_refund(
            s,
            tenant_id=who.tenant_id,
            register_id=body.register_id,
            cashier_id=who.cashier_id,
            role=role,
            sale_id=sale_id,
            idempotency_key=idempotency_key,
            lines=lines,
            reason=body.reason,
            settings=settings,
            now=now,
            approved_by=approved_by,
            actor_device_id=who.device_id,
            actor_session_id=who.session_id,
            before_commit=before_commit,
        )
        return JSONResponse(
            status_code=200 if replayed else 201,
            content=_refund_out(refund),
            headers={"Idempotent-Replayed": "true" if replayed else "false"},
        )

    @app.get("/api/v1/sales/{sale_id}/refunds")
    def sale_refunds(sale_id: str, who: SalesReader, s: Db):
        visible_sale(s, who, sale_id)
        return [_refund_out(r) for r in services.list_refunds(s, who.tenant_id, sale_id=sale_id)]

    @app.get("/api/v1/refunds/{refund_id}")
    def refund_get(refund_id: str, who: SalesReader, s: Db):
        refund = services.get_refund(s, who.tenant_id, refund_id)
        same_location(who, refund.register.location_id)
        return _refund_out(refund)

    @app.get("/api/v1/refunds/{refund_id}/receipt.txt", response_class=PlainTextResponse)
    def refund_receipt(refund_id: str, who: SalesReader, s: Db):
        refund = services.get_refund(s, who.tenant_id, refund_id)
        same_location(who, refund.register.location_id)
        return services.render_refund_receipt_text(refund, who.tenant_name, settings)

    @app.get("/api/v1/sales/{sale_id}/receipt.txt", response_class=PlainTextResponse)
    def receipt(sale_id: str, who: SalesReader, s: Db):
        sale = visible_sale(s, who, sale_id)
        return services.render_receipt_text(sale, who.tenant_name, settings)

    @app.get("/api/v1/reports/daily")
    def daily(who: Reporter, s: Db, business_date: date | None = None):
        # The report is tenant-wide: only owners and tenant-wide managers.
        if not who.demo and "owner" not in who.roles and None not in who.location_ids:
            raise services.Forbidden("the tenant-wide report needs an owner or tenant-wide manager")
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
    def tables(who: Waiter, s: Db):
        out = []
        for t in orders.list_tables(s, who.tenant_id):
            if who.location_id is not None and t.location_id != who.location_id:
                continue
            o = orders.open_order_for_table(s, who.tenant_id, t.id)
            out.append({"id": t.id, "code": t.code, "name": t.name, "location_id": t.location_id,
                        "open_order": None if o is None else {
                            "id": o.id, "version": o.version, "status": o.status,
                            "item_count": sum(ln.quantity for ln in o.lines)}})
        return out

    @app.post("/api/v1/tables/{table_id}/order")
    def table_order(table_id: int, who: Waiter, s: Db):
        """Get-or-create the table's open order (idempotent by design)."""
        same_location(who, orders.get_table(s, who.tenant_id, table_id).location_id)
        return order_out(orders.open_order(s, who.tenant_id, table_id, who.cashier_id, settings.clock()))

    @app.get("/api/v1/orders/{order_id}")
    def order_get(order_id: str, who: Waiter, s: Db):
        return order_out(visible_order(s, who, order_id))

    def visible_order(s: Session, who: auth.Principal, order_id: str):
        o = orders.get_order(s, who.tenant_id, order_id)
        same_location(who, o.location_id)
        return o

    @app.put("/api/v1/orders/{order_id}/lines")
    def order_lines(order_id: str, body: OrderLinesIn, who: Waiter, s: Db):
        visible_order(s, who, order_id)
        o = orders.set_lines(s, who.tenant_id, order_id, body.version,
                             [(x.product_id, x.quantity) for x in body.lines], settings.clock())
        return order_out(o)

    @app.post("/api/v1/orders/{order_id}/kitchen")
    def order_kitchen(order_id: str, body: OrderVersionIn, who: Waiter, s: Db,
                      idempotency_key: Annotated[str, Header(min_length=8, max_length=128)]):
        visible_order(s, who, order_id)
        ticket, replayed = orders.send_to_kitchen(s, who.tenant_id, order_id, body.version,
                                                  idempotency_key, settings.clock())
        return JSONResponse(status_code=200 if replayed else 201, content={
            "ticket": ticket_out(ticket), "order": order_out(orders.get_order(s, who.tenant_id, order_id))},
            headers={"Idempotent-Replayed": "true" if replayed else "false"})

    @app.post("/api/v1/orders/{order_id}/pay-cash")
    def order_pay(order_id: str, body: PayOrderIn, who: Seller, s: Db,
                  idempotency_key: Annotated[str, Header(min_length=8, max_length=128)]):
        till_register(who, body.register_id)
        visible_order(s, who, order_id)
        sale, replayed = orders.pay_order_cash(
            s, tenant_id=who.tenant_id, order_id=order_id, expected_version=body.version,
            register_id=body.register_id, cashier_id=who.cashier_id, tendered_cents=body.tendered_cents,
            idempotency_key=idempotency_key, settings=settings, now=settings.clock(),
            actor_device_id=who.device_id, actor_session_id=who.session_id)
        return JSONResponse(status_code=200 if replayed else 201, content=_sale_out(sale),
                            headers={"Idempotent-Replayed": "true" if replayed else "false"})

    # ---------------------------------------------------------- bill payments (C3)

    Receivables = Annotated[auth.Principal, Depends(need("receivables"))]

    def invoice_out(i) -> dict:
        return {"id": i.id, "invoice_no": i.invoice_no, "reference_no": i.reference_no, "order_id": i.order_id,
                "sale_id": i.sale_id, "customer_name": i.customer_name, "customer_code": i.customer_code,
                "amount_cents": i.amount_cents, "paid_cents": i.paid_cents,
                "outstanding_cents": i.amount_cents - i.paid_cents, "status": i.status,
                "due_date": i.due_date.isoformat(), "created_at": i.created_at.isoformat()}

    @app.get("/api/v1/orders/{order_id}/bill")
    def bill_get(order_id: str, who: Seller, s: Db):
        o = visible_order(s, who, order_id)
        return payments.bill_out(o, payments.bill_state(s, who.tenant_id, o, settings.clock(), settings))

    @app.post("/api/v1/orders/{order_id}/payments")
    def bill_pay(order_id: str, body: BillPaymentIn, who: Seller, s: Db,
                 idempotency_key: Annotated[str, Header(min_length=8, max_length=128)]):
        till_register(who, body.register_id)
        visible_order(s, who, order_id)
        common = dict(tenant_id=who.tenant_id, order_id=order_id, register_id=body.register_id,
                      expected_total_cents=body.expected_total_cents, amount_cents=body.amount_cents,
                      idempotency_key=idempotency_key, actor=who.cashier_id, settings=settings,
                      now=settings.clock())
        if body.method == "cash":
            p, replayed = payments.pay_cash(s, tendered_cents=body.tendered_cents, **common)
        elif body.method == "card":
            p, replayed = payments.pay_card(s, simulate=body.simulate, **common)
        else:
            p, replayed = payments.pay_transfer(s, customer_name=body.customer_name,
                                                customer_code=body.customer_code, due_days=body.due_days,
                                                **common)
        o = orders.get_order(s, who.tenant_id, order_id)
        bill = payments.bill_out(o, payments.bill_state(s, who.tenant_id, o, settings.clock(), settings))
        payment = next(x for x in bill["payments"] if x["id"] == p.id)
        return JSONResponse(status_code=200 if replayed else 201, content={"payment": payment, "bill": bill},
                            headers={"Idempotent-Replayed": "true" if replayed else "false"})

    @app.post("/api/v1/payments/{payment_id}/reconcile")
    def card_reconcile(payment_id: str, who: Seller, s: Db):
        """Ask the (MOCK) provider what happened to a card payment whose answer was lost."""
        visible_order(s, who, payments.get_payment(s, who.tenant_id, payment_id).order_id)
        p = payments.reconcile_card(s, tenant_id=who.tenant_id, payment_id=payment_id, settings=settings,
                                    now=settings.clock())
        o = visible_order(s, who, p.order_id)
        return payments.bill_out(o, payments.bill_state(s, who.tenant_id, o, settings.clock(), settings))

    @app.get("/api/v1/receivables/invoices")
    def receivable_invoices(who: Receivables, s: Db):
        return [invoice_out(i) for i in payments.invoices(s, who.tenant_id)]

    @app.post("/api/v1/receivables/bank-import")
    def bank_import(body: BankImportIn, who: Receivables, s: Db):
        """SANDBOX statement import (no real bank connection): match by reference."""
        return payments.import_bank_lines(
            s, tenant_id=who.tenant_id, settings=settings, now=settings.clock(),
            lines=[payments.BankLine(x.bank_tx_id, x.booked_date, x.amount_cents, x.reference, x.payer)
                   for x in body.lines])

    @app.get("/api/v1/kitchen/tickets")
    def kitchen_tickets(who: KitchenReader, s: Db):
        return [ticket_out(t) for t in orders.list_tickets(s, who.tenant_id)
                if who.location_id is None or t.location_id == who.location_id]

    @app.post("/api/v1/kitchen/tickets/{ticket_id}/ready")
    def kitchen_ready(ticket_id: str, who: KitchenUser, s: Db):
        same_location(who, orders.get_ticket(s, who.tenant_id, ticket_id).location_id)
        return ticket_out(orders.mark_ready(s, who.tenant_id, ticket_id, settings.clock()))

    @app.get("/api/v1/operations/{idempotency_key}")
    def operation(idempotency_key: str, who: SalesReader, s: Db):
        """Was a sale/refund with this key recorded? (tenant-scoped)"""
        return orders.operation_status(s, who.tenant_id, idempotency_key)

    # ------------------------------------------------------------ realtime (SSE)

    @app.get("/api/v1/events/stream")
    async def event_stream(
        request: Request,
        who: Who,
        s: Db,
        last_event_id: Annotated[str | None, Header()] = None,
        max_s: Annotated[int, Query(ge=1, le=300)] = 300,
        after: Annotated[int | None, Query(ge=0)] = None,
    ):
        """Server-sent events: invalidation messages (ids + versions, never
        payload data) for the caller's tenant and location, in commit order.

        Resume: ``Last-Event-ID`` (or ``?after=``) = last seen sequence; with
        neither, the stream starts at the current head and the client loads a
        snapshot first. The stream ends after ``max_s`` seconds (the client
        reconnects with its cursor), when the session ends, or when the client
        disconnects. A heartbeat comment keeps proxies from closing it.
        """
        s.close()  # do not hold a pooled connection for the life of the stream
        bearer = request.headers.get("authorization", "")[7:] or None
        device_token = request.headers.get("x-device-token")
        cursor = after if after is not None else (
            int(last_event_id) if last_event_id and last_event_id.isdigit() else None)

        def fetch(cur: int | None) -> tuple[int, list[dict]]:
            with factory() as s2:
                bind_tenant(s2, who.tenant_id)
                if cur is None:
                    return events.latest_seq(s2, who.tenant_id), []
                rows = events.events_after(s2, who.tenant_id, cur, 200)
                head = rows[-1].recorded_seq if rows else cur  # also past events hidden by location
                scope = who.visible_locations()  # current principal (refreshed on every re-check)
                return head, [events.invalidation(e) for e in rows
                              if scope is None or e.location_id is None or e.location_id in scope]

        def still_valid() -> bool:
            """Re-authorize with the CURRENT session state (revocation, idle,
            roles); never extends the session. The scope used for the next
            batches is the refreshed principal's."""
            nonlocal who
            if who.demo:
                return True
            with factory() as s3:
                try:
                    fresh = auth.resolve_session(s3, bearer=bearer, device_token=device_token, settings=settings,
                                                 now=settings.clock(), touch=False)
                except auth.Unauthorized:
                    return False
            if not fresh.roles:
                return False
            who = fresh
            return True

        async def gen():
            nonlocal cursor
            started = time.monotonic()
            last_beat = last_auth = started
            cursor, _ = (await run_in_threadpool(fetch, None)) if cursor is None else (cursor, [])
            yield f"retry: 2000\nevent: hello\ndata: {json.dumps({'seq': cursor})}\n\n"
            while time.monotonic() - started < max_s:
                if await request.is_disconnected():
                    return
                cursor, items = await run_in_threadpool(fetch, cursor)
                for item in items:
                    yield f"id: {item['seq']}\nevent: {item['type']}\ndata: {json.dumps(item)}\n\n"
                now_m = time.monotonic()
                if now_m - last_auth >= settings.stream_auth_recheck_s:
                    last_auth = now_m
                    if not await run_in_threadpool(still_valid):
                        yield "event: auth\ndata: {\"reason\": \"session ended\"}\n\n"
                        return
                if now_m - last_beat >= 15:
                    last_beat = now_m
                    yield ": heartbeat\n\n"
                await asyncio.sleep(settings.stream_poll_s)
            yield f"event: bye\ndata: {json.dumps({'seq': cursor})}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/admin/event-health")
    def event_health(who: Who, s: Db):
        auth.require_admin(who)
        return {"head": events.latest_seq(s, who.tenant_id), "consumers": events.consumer_health(s, who.tenant_id)}

    # ------------------------------------------------------------ auth (RFC-0002)

    def require_box() -> auth.SecretBox:
        if box is None:
            raise HTTPException(503, "AXAR_POS_SECRET_KEY is not configured")
        return box

    def throttle(request: Request, what: str) -> None:
        if not login_limiter.hit(f"{what}:{client_ip(request)}"):
            raise auth.Locked("too many attempts from this address; wait a few minutes")

    def staff_out(st) -> dict:
        return {"id": st.id, "display_name": st.display_name, "active": st.active,
                "roles": [{"role": r.role, "location_id": r.location_id} for r in st.roles],
                "has_pin": st.pin_hash is not None, "login": st.login,
                "has_back_office_login": st.password_hash is not None}

    def device_out(d) -> dict:
        return {"id": d.id, "name": d.name, "location_id": d.location_id, "register_id": d.register_id,
                "created_at": d.created_at.isoformat(), "revoked": d.revoked_at is not None,
                "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None}

    def session_out(sess, raw: str, st) -> dict:
        return {"token": raw, "expires_at": sess.expires_at.isoformat(), "kind": sess.kind,
                "idle_minutes": settings.till_idle_minutes if sess.kind == "till" else settings.admin_idle_minutes,
                "staff": {"id": st.id, "display_name": st.display_name}}

    def the_device(s: Session, x_device_token: str | None):
        d = auth.device_by_token(s, x_device_token)
        if d is None:
            raise auth.Unauthorized("this device is not enrolled (or was revoked)")
        return d

    @app.get("/api/v1/auth/config")
    def auth_config():
        return {"mode": "demo" if settings.demo_identity else "server",
                "back_office_login": box is not None}

    @app.post("/api/v1/auth/devices/enroll", status_code=201)
    def enroll(body: EnrollIn, request: Request, s: Db):
        throttle(request, "enroll")
        dev, raw = auth.enroll_device(s, code=body.code, ip=client_ip(request), now=settings.clock())
        return {"device_token": raw, "device": device_out(dev)}

    @app.get("/api/v1/auth/device")
    def device_info(s: Db, x_device_token: Annotated[str | None, Header()] = None):
        d = the_device(s, x_device_token)
        staff = auth.pin_staff_list(s, d)
        tenant = s.get(Tenant, d.tenant_id)
        reg = s.get(auth.Register, d.register_id) if d.register_id else None
        loc = s.get(auth.Location, d.location_id)
        return {"device": device_out(d), "tenant_name": tenant.name, "location_name": loc.name,
                "register_name": reg.name if reg else None,
                "staff": [{"id": x.id, "display_name": x.display_name,
                           "can_approve": bool(auth.roles_at(x, d.location_id) & set(auth.MANAGER_ROLES))}
                          for x in staff]}

    @app.post("/api/v1/auth/pin-login")
    def pin_login(body: PinLoginIn, request: Request, s: Db,
                  x_device_token: Annotated[str | None, Header()] = None):
        d = the_device(s, x_device_token)
        sess, raw, st = auth.pin_login(s, device=d, staff_id=body.staff_id, pin=body.pin, settings=settings,
                                       now=settings.clock(), ip=client_ip(request))
        return session_out(sess, raw, st)

    @app.post("/api/v1/auth/login")
    def password_login(body: PasswordLoginIn, request: Request, s: Db):
        throttle(request, "login")
        sess, raw, st = auth.password_login(s, tenant_id=body.tenant_id, login=body.login, password=body.password,
                                            totp=body.totp, box=require_box(), settings=settings,
                                            now=settings.clock(), ip=client_ip(request))
        return session_out(sess, raw, st)

    @app.post("/api/v1/auth/logout", status_code=204)
    def do_logout(who: Who, s: Db):
        if not who.demo:
            auth.logout(s, who, settings.clock())

    @app.get("/api/v1/auth/me")
    def me(who: Who):
        return {"tenant_id": who.tenant_id, "tenant_name": who.tenant_name, "staff_id": who.staff_id,
                "display_name": who.staff_name, "roles": sorted(who.roles), "kind": who.kind, "mfa": who.mfa,
                "device_id": who.device_id, "location_id": who.location_id, "register_id": who.register_id,
                "permissions": sorted(p for p in auth.PERMISSIONS if who.can(p)), "demo": who.demo}

    @app.post("/api/v1/auth/approvals", status_code=201)
    def approve(body: ApprovalIn, request: Request, who: Who, s: Db,
                x_device_token: Annotated[str | None, Header()] = None):
        if who.demo:
            raise HTTPException(400, "approvals are not used in demo identity mode")
        d = the_device(s, x_device_token)
        if body.action == "refund":
            r = RefundApprovalRequest.model_validate(body.request)
            if not who.can("refund.request"):
                raise services.Forbidden("your role may not request refunds")
            req_hash = services._refund_request_hash(
                r.sale_id, r.register_id, [services.RefundLineRequest(x.line_no, x.quantity) for x in r.lines],
                r.reason)
        else:
            r = DrawerCloseApprovalRequest.model_validate(body.request)
            req_hash = drawer_close_hash(r.drawer_id, r.counted_cents)
        a = auth.create_approval(s, who, device=d, action=body.action, request_hash=req_hash,
                                 approver_id=body.approver_id, pin=body.pin, now=settings.clock(),
                                 ip=client_ip(request))
        return {"approval_id": a.id, "expires_at": a.expires_at.isoformat(), "approver_role": a.approver_role}

    # ------------------------------------------------- back office (MFA only)

    @app.get("/api/v1/admin/staff")
    def admin_staff(who: Who, s: Db):
        auth.require_admin(who)
        return [staff_out(x) for x in s.scalars(select(auth.Staff).where(auth.Staff.tenant_id == who.tenant_id)
                                                 .order_by(auth.Staff.display_name))]

    @app.post("/api/v1/admin/staff", status_code=201)
    def admin_create_staff(body: StaffIn, who: Who, s: Db):
        st = auth.create_staff(s, who, display_name=body.display_name,
                               roles=[(r.role, r.location_id) for r in body.roles], pin=body.pin,
                               now=settings.clock())
        return staff_out(st)

    @app.put("/api/v1/admin/staff/{staff_id}/pin", status_code=204)
    def admin_set_pin(staff_id: str, body: PinIn, who: Who, s: Db):
        auth.set_pin(s, who, staff_id, body.pin, settings.clock())

    @app.put("/api/v1/admin/staff/{staff_id}/credentials")
    def admin_credentials(staff_id: str, body: CredentialsIn, who: Who, s: Db):
        secret = auth.set_back_office_credentials(s, who, staff_id, login=body.login, password=body.password,
                                                  box=require_box(), now=settings.clock())
        # Shown ONCE so it can be added to an authenticator app; never stored in clear.
        return {"totp_secret": secret,
                "otpauth_uri": auth.provisioning_uri(secret, f"{who.tenant_id}:{body.login}")}

    @app.post("/api/v1/admin/staff/{staff_id}/deactivate")
    def admin_deactivate(staff_id: str, who: Who, s: Db):
        return staff_out(auth.deactivate_staff(s, who, staff_id, settings.clock()))

    @app.post("/api/v1/admin/enrollment-codes", status_code=201)
    def admin_enroll_code(body: EnrollCodeIn, who: Who, s: Db):
        ec, code = auth.create_enrollment_code(s, who, location_id=body.location_id, register_id=body.register_id,
                                               device_name=body.device_name, now=settings.clock())
        return {"code": code, "expires_at": ec.expires_at.isoformat()}

    @app.get("/api/v1/admin/devices")
    def admin_devices(who: Who, s: Db):
        auth.require_admin(who)
        return [device_out(d) for d in s.scalars(select(auth.Device).where(auth.Device.tenant_id == who.tenant_id))
                if "owner" in who.roles or None in who.location_ids or d.location_id in who.location_ids]

    @app.post("/api/v1/admin/devices/{device_id}/revoke")
    def admin_revoke(device_id: str, who: Who, s: Db):
        return device_out(auth.revoke_device(s, who, device_id, settings.clock()))

    # ------------------------------------------- receipt series + journal (O1a)

    @app.get("/api/v1/admin/registers/{register_id}/receipt-series")
    def receipt_series_get(register_id: int, who: Who, s: Db):
        reg = services.get_register(s, who.tenant_id, register_id)
        auth.require_admin(who, reg.location_id)
        return _series_out(s, who.tenant_id, reg)

    @app.put("/api/v1/admin/registers/{register_id}/receipt-series")
    def receipt_series_put(register_id: int, body: ReceiptSeriesIn, who: Who, s: Db):
        reg = s.scalar(select(Register).where(Register.id == register_id, Register.tenant_id == who.tenant_id)
                       .with_for_update(of=Register))
        if reg is None:
            raise services.NotFound("register not found")
        auth.require_admin(who, reg.location_id)
        last = _series_last(s, who.tenant_id, reg)
        if body.next_number <= last:
            raise services.Conflict(f"next_number must be greater than the last issued number {last} "
                                    "(receipt numbers are never reused)")
        before = {"prefix": reg.receipt_prefix, "floor": reg.receipt_floor}
        reg.receipt_prefix, reg.receipt_floor = body.prefix, body.next_number
        auth.audit(s, settings.clock(), "receipt_series.changed", "ok", tenant_id=who.tenant_id,
                   staff_id=who.staff_id, device_id=who.device_id, session_id=who.session_id,
                   detail=json.dumps({"register_id": reg.id, "before": before,
                                      "after": {"prefix": body.prefix, "floor": body.next_number}}))
        s.commit()
        return _series_out(s, who.tenant_id, reg)

    def _series_last(s: Session, tenant_id: str, reg) -> int:
        from sqlalchemy import func as _f

        return s.scalar(select(_f.coalesce(_f.max(Sale.register_receipt_no), 0)).where(
            Sale.tenant_id == tenant_id, Sale.register_id == reg.id)) or 0

    def _series_out(s: Session, tenant_id: str, reg) -> dict:
        last = _series_last(s, tenant_id, reg)
        nxt = max(last + 1, reg.receipt_floor or 1)
        return {"register_id": reg.id, "prefix": reg.receipt_prefix, "last_number": last, "next_number": nxt,
                "next_label": f"{reg.receipt_prefix}-{nxt:06d}" if reg.receipt_prefix else None}

    @app.get("/api/v1/admin/registers/{register_id}/journal/verify")
    def journal_verify(register_id: int, who: Who, s: Db):
        """Recompute the register's hash-chained journal against the records."""
        from . import journal

        reg = services.get_register(s, who.tenant_id, register_id)
        auth.require_admin(who, reg.location_id)
        return journal.verify(s, who.tenant_id, journal.stream_for_register(reg.id))

    # ------------------------------------------------- offline sync (O1b)

    @app.post("/api/v1/sync/push")
    def sync_push(body: SyncPushIn, who: Seller, s: Db):
        """A till pushes its journal (ADR-0012 §7). A device may only push its
        OWN stream; demo identity (tests/dev) may name the device."""
        if not who.demo and who.device_id != body.device_id:
            raise services.Forbidden("a device may only push its own journal")
        for e in body.entries:
            if not isinstance(e.entry.get("seq"), int) or not isinstance(e.entry.get("id"), str):
                raise services.DomainError("each entry needs an integer seq and an id")
        out = sync.ingest(s, tenant_id=who.tenant_id, device_id=body.device_id,
                          items=[sync.Item(e.entry, e.payload, e.payload_hash) for e in body.entries],
                          settings=settings, now=settings.clock())
        out["policy"] = {"offline_limit_hours": offline.offline_limit_hours(s, who.tenant_id)}
        return out

    @app.get("/api/v1/sync/status")
    def sync_status(who: Seller, s: Db):
        """EDGE: may this till sell, and for how long without contact."""
        if not settings.edge_device_id:
            raise services.NotFound("not an edge node")
        return offline.status(s, tenant_id=who.tenant_id, device_id=settings.edge_device_id, now=settings.clock())

    @app.get("/api/v1/admin/pos-settings")
    def pos_settings_get(who: Who, s: Db):
        auth.require_admin(who)
        return {"offline_limit_hours": offline.offline_limit_hours(s, who.tenant_id),
                "offline_limit_max_hours": offline.MAX_OFFLINE_HOURS}

    @app.put("/api/v1/admin/pos-settings")
    def pos_settings_put(body: PosSettingsIn, who: Who, s: Db):
        auth.require_admin(who)   # no location given: owner or tenant-wide manager only (applies everywhere)
        hours = offline.set_offline_limit(s, tenant_id=who.tenant_id, hours=body.offline_limit_hours,
                                          actor=who.staff_id or who.cashier_id, now=settings.clock())
        return {"offline_limit_hours": hours, "offline_limit_max_hours": offline.MAX_OFFLINE_HOURS}

    def inbox_out(r) -> dict:
        return {"id": r.id, "device_id": r.device_id, "seq": r.seq, "entry_id": r.entry_id,
                "entry_type": r.entry_type, "status": r.status, "reason": r.reason, "sale_id": r.sale_id,
                "payload": json.loads(r.payload), "received_at": r.received_at.isoformat(),
                "resolved_by": r.resolved_by, "resolution_note": r.resolution_note}

    @app.get("/api/v1/sync/quarantine")
    def sync_quarantine(who: Reporter, s: Db):
        return [inbox_out(r) for r in sync.quarantine(s, who.tenant_id)]

    @app.post("/api/v1/sync/quarantine/{row_id}/retry")
    def sync_retry(row_id: int, who: Reporter, s: Db):
        return inbox_out(sync.retry(s, tenant_id=who.tenant_id, row_id=row_id, actor=who.cashier_id,
                                    settings=settings, now=settings.clock()))

    @app.post("/api/v1/sync/quarantine/{row_id}/dismiss")
    def sync_dismiss(row_id: int, body: DismissIn, who: Reporter, s: Db):
        return inbox_out(sync.dismiss(s, tenant_id=who.tenant_id, row_id=row_id, actor=who.cashier_id,
                                      note=body.note, now=settings.clock()))

    @app.get("/api/v1/admin/auth-events")
    def admin_events(who: Who, s: Db, limit: Annotated[int, Query(ge=1, le=500)] = 100):
        auth.require_admin(who)
        if "owner" not in who.roles and None not in who.location_ids:
            raise services.Forbidden("the audit log needs an owner or tenant-wide manager")
        rows = s.scalars(select(auth.AuthEvent).where(auth.AuthEvent.tenant_id == who.tenant_id)
                         .order_by(auth.AuthEvent.id.desc()).limit(limit))
        return [{"at": e.at.isoformat(), "event": e.event, "outcome": e.outcome, "staff_id": e.staff_id,
                 "device_id": e.device_id, "detail": e.detail} for e in rows]

    # ------------------------------------------------------ inventory (#14)

    Stockkeeper = Annotated[auth.Principal, Depends(need("inventory"))]

    def doc_out(d) -> dict:
        return {"id": d.id, "supplier_id": d.supplier_id, "doc_type": d.doc_type, "doc_no": d.doc_no,
                "doc_date": d.doc_date.isoformat(), "warehouse_id": d.warehouse_id, "status": d.status,
                "freight_cents": d.freight_cents, "matches_document_id": d.matches_document_id,
                "lines": [{"line_no": ln.line_no, "item_id": ln.item_id, "qty_micro": ln.qty_micro,
                           "net_cents": ln.net_cents, "vat_rate_bp": ln.vat_rate_bp,
                           "vat_recoverable": ln.vat_recoverable} for ln in d.lines]}

    @app.post("/api/v1/inventory/items", status_code=201)
    def inv_item(body: ItemIn, who: Stockkeeper, s: Db):
        it = inventory.create_item(s, who.tenant_id, sku=body.sku, name=body.name, kind=body.kind, uom=body.uom)
        return {"id": it.id, "sku": it.sku, "name": it.name, "kind": it.kind, "uom": it.uom}

    @app.post("/api/v1/inventory/warehouses", status_code=201)
    def inv_wh(body: WarehouseIn, who: Stockkeeper, s: Db):
        w = inventory.create_warehouse(s, who.tenant_id, location_id=body.location_id, code=body.code,
                                       name=body.name, is_default=body.is_default)
        return {"id": w.id, "code": w.code, "location_id": w.location_id, "is_default": w.is_default}

    @app.post("/api/v1/inventory/suppliers", status_code=201)
    def inv_supplier(body: SupplierIn, who: Stockkeeper, s: Db):
        sp = inventory.create_supplier(s, who.tenant_id, code=body.code, name=body.name)
        return {"id": sp.id, "code": sp.code, "name": sp.name}

    @app.post("/api/v1/inventory/documents", status_code=201)
    def inv_doc(body: SupplierDocIn, who: Stockkeeper, s: Db):
        d = inventory.create_document(
            s, who.tenant_id, supplier_id=body.supplier_id, doc_type=body.doc_type, doc_no=body.doc_no,
            doc_date=body.doc_date, warehouse_id=body.warehouse_id, freight_cents=body.freight_cents,
            lines=[inventory.DocLine(x.item_id, x.qty_micro, x.net_cents, x.vat_rate_bp, x.vat_recoverable)
                   for x in body.lines],
            actor=who.staff_id, now=settings.clock(), matches_document_id=body.matches_document_id)
        return doc_out(d)

    @app.post("/api/v1/inventory/documents/{document_id}/post")
    def inv_post(document_id: str, who: Stockkeeper, s: Db):
        return doc_out(inventory.post_document(s, who.tenant_id, document_id, actor=who.staff_id,
                                               now=settings.clock()))

    @app.post("/api/v1/inventory/documents/{document_id}/returns", status_code=201)
    def inv_return(document_id: str, body: ReturnIn, who: Stockkeeper, s: Db):
        m = inventory.return_to_supplier(s, who.tenant_id, document_id, item_id=body.item_id,
                                         qty_micro=body.qty_micro, return_no=body.return_no, now=settings.clock())
        return {"qty_micro": m.qty_micro, "value_cents": m.value_cents}

    @app.post("/api/v1/inventory/recipes", status_code=201)
    def inv_recipe(body: RecipeIn, who: Stockkeeper, s: Db):
        r = inventory.set_recipe(s, who.tenant_id, product_id=body.product_id, output_item_id=body.output_item_id,
                                 lines=[(x.item_id, x.qty_micro) for x in body.lines], yield_micro=body.yield_micro,
                                 valid_from=body.valid_from, now=settings.clock())
        return {"id": r.id, "version": r.version, "valid_from": r.valid_from.isoformat()}

    @app.post("/api/v1/inventory/sell-item")
    def inv_sell_item(body: SellItemIn, who: Stockkeeper, s: Db):
        p = inventory.sell_item(s, who.tenant_id, product_id=body.product_id, item_id=body.item_id)
        return {"product_id": p.id, "stock_mode": p.stock_mode, "stock_item_id": p.stock_item_id}

    @app.post("/api/v1/inventory/production", status_code=201)
    def inv_produce(body: ProduceIn, who: Stockkeeper, s: Db,
                    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)]):
        b = inventory.produce(s, who.tenant_id, output_item_id=body.output_item_id, warehouse_id=body.warehouse_id,
                              batches=body.batches, idempotency_key=idempotency_key, actor=who.staff_id,
                              now=settings.clock())
        return {"id": b.id, "output_qty_micro": b.output_qty_micro, "cost_cents": b.cost_cents}

    @app.get("/api/v1/inventory/stock")
    def inv_stock(who: Stockkeeper, s: Db):
        return inventory.stock(s, who.tenant_id)

    @app.get("/api/v1/inventory/items/{item_id}/card")
    def inv_card(item_id: int, who: Stockkeeper, s: Db):
        return inventory.stock_card(s, who.tenant_id, item_id)

    @app.get("/api/v1/inventory/reconciliation")
    def inv_recon(who: Stockkeeper, s: Db):
        return inventory.reconcile(s, who.tenant_id)

    @app.get("/api/v1/inventory/margin")
    def inv_margin(who: Stockkeeper, s: Db, business_date: date | None = None):
        bdate = business_date or services.business_date_for(settings.clock(), settings)
        rep = services.daily_summary(s, who.tenant_id, bdate)
        revenue = rep["net_total_cents"] - rep["net_vat_cents"]  # net of VAT, after refunds
        cost = inventory.cogs(s, who.tenant_id, bdate)
        return {"business_date": bdate.isoformat(), "net_revenue_cents": revenue, "cogs_cents": cost,
                "gross_margin_cents": revenue - cost}

    def recipe_out(r) -> dict | None:
        if r is None:
            return None
        return {"id": r.id, "version": r.version, "valid_from": r.valid_from.isoformat(), "product_id": r.product_id,
                "output_item_id": r.output_item_id, "yield_micro": r.yield_micro,
                "lines": [{"item_id": ln.item_id, "qty_micro": ln.qty_micro} for ln in r.lines]}

    @app.get("/api/v1/inventory/items")
    def inv_items(who: Stockkeeper, s: Db):
        return [{"id": i.id, "sku": i.sku, "name": i.name, "kind": i.kind, "uom": i.uom}
                for i in s.scalars(select(inventory.Item).where(inventory.Item.tenant_id == who.tenant_id)
                                   .order_by(inventory.Item.name))]

    @app.get("/api/v1/inventory/suppliers")
    def inv_suppliers(who: Stockkeeper, s: Db):
        return [{"id": x.id, "code": x.code, "name": x.name}
                for x in s.scalars(select(inventory.Supplier).where(inventory.Supplier.tenant_id == who.tenant_id)
                                   .order_by(inventory.Supplier.name))]

    @app.get("/api/v1/inventory/warehouses")
    def inv_warehouses(who: Stockkeeper, s: Db):
        return [{"id": w.id, "code": w.code, "name": w.name, "location_id": w.location_id, "is_default": w.is_default}
                for w in s.scalars(select(inventory.Warehouse).where(inventory.Warehouse.tenant_id == who.tenant_id))]

    @app.get("/api/v1/inventory/documents")
    def inv_docs(who: Stockkeeper, s: Db, status: Literal["draft", "posted"] | None = None):
        return [{**doc_out(d), **inventory.document_total(d)} for d in inventory.list_documents(s, who.tenant_id, status)]

    @app.get("/api/v1/inventory/documents/{document_id}")
    def inv_doc_get(document_id: str, who: Stockkeeper, s: Db):
        d = s.scalar(select(inventory.SupplierDocument).where(inventory.SupplierDocument.id == document_id,
                                                              inventory.SupplierDocument.tenant_id == who.tenant_id))
        if d is None:
            raise services.NotFound("document not found")
        return {**doc_out(d), **inventory.document_total(d)}

    @app.get("/api/v1/inventory/recipes")
    def inv_recipe_get(who: Stockkeeper, s: Db, product_id: int | None = None, output_item_id: int | None = None):
        on = services.business_date_for(settings.clock(), settings)
        return recipe_out(inventory.recipe_for(s, who.tenant_id, product_id=product_id,
                                               output_item_id=output_item_id, on=on))

    @app.post("/api/v1/inventory/recipes/preview")
    def inv_recipe_preview(body: RecipePreviewIn, who: Stockkeeper, s: Db):
        return inventory.preview_recipe(s, who.tenant_id, product_id=body.product_id,
                                        lines=[(x.item_id, x.qty_micro) for x in body.lines],
                                        yield_micro=body.yield_micro,
                                        on=services.business_date_for(settings.clock(), settings))

    @app.get("/api/v1/inventory/offers")
    def inv_offers(who: Stockkeeper, s: Db):
        return [{"id": p.id, "sku": p.sku, "name": p.name, "price_cents": p.price_cents, "stock_mode": p.stock_mode,
                 "stock_item_id": p.stock_item_id}
                for p in services.list_products(s, who.tenant_id)]

    return app
