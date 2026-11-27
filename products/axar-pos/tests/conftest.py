from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from axar_pos.api import create_app
from axar_pos.config import Settings
from axar_pos.models import DiningTable, Location, Product, Register, Tenant


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def clock() -> Clock:
    # 2026-03-10 10:00 UTC == 12:00 Europe/Tallinn (EET, UTC+2)
    return Clock(datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc))


# Set AXAR_TEST_PG=postgresql+psycopg://user@host:port to run the whole suite
# on PostgreSQL: every test gets its own freshly created database.
PG_BASE = os.environ.get("AXAR_TEST_PG")


# Test-only credentials of the non-owner app role on a throwaway test server.
APP_ROLE_PASSWORD = "axar_app_test_only"


def app_role_url(admin_url: str) -> str:
    """Same server/database, connecting as the RLS-bound role ``axar_app``."""
    from sqlalchemy.engine import make_url

    return make_url(admin_url).set(username="axar_app", password=APP_ROLE_PASSWORD) \
        .render_as_string(hide_password=False)


@pytest.fixture
def database_urls(tmp_path):
    """(app_url, admin_url). On PostgreSQL: a fresh database per test."""
    if not PG_BASE:
        yield f"sqlite:///{tmp_path / 'pos.db'}", None
        return
    name = f"axar_t_{uuid.uuid4().hex[:12]}"
    admin = create_engine(f"{PG_BASE}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if c.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = 'axar_app'")) is None:
            c.execute(text(f"CREATE ROLE axar_app LOGIN PASSWORD '{APP_ROLE_PASSWORD}'"))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield app_role_url(f"{PG_BASE}/{name}"), f"{PG_BASE}/{name}"
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def settings(clock, database_urls) -> Settings:
    app_url, admin_url = database_urls
    return Settings(
        database_url=app_url,
        admin_database_url=admin_url,
        demo_identity=True,
        clock=clock,
    )


@pytest.fixture
def app(settings):
    app = create_app(settings)
    with app.state.session_factory.begin() as s:
        for tid, name in (("t-a", "Kohvik A"), ("t-b", "Pood B")):
            s.add(Tenant(id=tid, name=name))
        s.flush()
        s.add_all([
            Product(tenant_id="t-a", sku="KOHV", name="Kohv", price_cents=250),
            Product(tenant_id="t-a", sku="SAI", name="Saiake", price_cents=199),
            Product(tenant_id="t-a", sku="RAAMAT", name="Raamat", price_cents=1599,
                    tax_category="reduced"),
            Product(tenant_id="t-b", sku="B-SECRET", name="B toode", price_cents=1000),
        ])
        la = Location(tenant_id="t-a", code="L1", name="Kohvik A kesklinn")
        lb = Location(tenant_id="t-b", code="L1", name="Pood B")
        s.add_all([la, lb])
        s.flush()
        s.add_all([
            Register(tenant_id="t-a", location_id=la.id, code="R1", name="Kassa 1"),
            Register(tenant_id="t-a", location_id=la.id, code="R2", name="Kassa 2"),
            Register(tenant_id="t-b", location_id=lb.id, code="R1", name="B kassa"),
            DiningTable(tenant_id="t-a", location_id=la.id, code="A1", name="Laud A1"),
            DiningTable(tenant_id="t-a", location_id=la.id, code="A2", name="Laud A2"),
            DiningTable(tenant_id="t-b", location_id=lb.id, code="B1", name="Laud B1"),
        ])
    return app


class Api:
    """Thin helper that always sends a tenant identity."""

    def __init__(self, client: TestClient, tenant: str, cashier: str = "kassa1",
                 role: str | None = None):
        self.c = client
        self.h = {"X-Tenant-ID": tenant, "X-Cashier-ID": cashier}
        if role:
            self.h["X-Role"] = role

    def as_role(self, role: str, cashier: str = "juhataja") -> "Api":
        return Api(self.c, self.h["X-Tenant-ID"], cashier, role)

    def refund(self, sale_id, lines, key=None, register="R1", reason="klient tagastas"):
        body = {"register_id": self.register_id(register),
                "lines": [{"line_no": n, "quantity": q} for n, q in lines],
                "reason": reason}
        return self.post(f"/api/v1/sales/{sale_id}/refunds", body,
                         headers={"Idempotency-Key": key or str(uuid.uuid4())})

    def get(self, path, **kw):
        return self.c.get(path, headers=self.h, **kw)

    def post(self, path, json=None, headers=None):
        return self.c.post(path, json=json, headers={**self.h, **(headers or {})})

    def put(self, path, json=None, headers=None):
        return self.c.put(path, json=json, headers={**self.h, **(headers or {})})

    def product_id(self, sku: str) -> int:
        return next(p["id"] for p in self.get("/api/v1/products").json() if p["sku"] == sku)

    def register_id(self, code: str = "R1") -> int:
        return next(r["id"] for r in self.get("/api/v1/registers").json() if r["code"] == code)

    def open_drawer(self, float_cents=5000, register="R1"):
        r = self.post("/api/v1/drawer/open", {"register_id": self.register_id(register),
                                              "opening_float_cents": float_cents})
        assert r.status_code == 201, r.text
        return r.json()

    def drawer(self, register="R1"):
        return self.get(f"/api/v1/registers/{self.register_id(register)}/drawer").json()["drawer"]

    def sale_body(self, lines, tendered, register="R1"):
        return {"register_id": self.register_id(register),
                "lines": [{"product_id": self.product_id(sku), "quantity": q} for sku, q in lines],
                "tendered_cents": tendered}

    def sell(self, lines, tendered, key=None, register="R1"):
        return self.post("/api/v1/sales/cash", self.sale_body(lines, tendered, register),
                         headers={"Idempotency-Key": key or str(uuid.uuid4())})


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c
    app.state.engine.dispose()
    app.state.admin_engine.dispose()


@pytest.fixture
def a(client) -> Api:
    return Api(client, "t-a")


@pytest.fixture
def b(client) -> Api:
    return Api(client, "t-b")


# ------------------------------------------------- input-path coverage guard
# Every write endpoint (an "input" into AXAR) must be exercised by at least one
# test that checks where its data lands. The recorder notes which route
# templates the tests call with 2xx; a full run fails if a write route was
# never exercised successfully (tester's guard, 13-delivery-journey.md §7).

_CALLED: set[tuple[str, str]] = set()
_ALL_WRITES: set[tuple[str, str]] = set()


def _route_of(app, method: str, path: str) -> str | None:
    from starlette.routing import Match

    scope = {"type": "http", "method": method, "path": path.split("?")[0], "root_path": "", "query_string": b"",
             "headers": []}
    for r in app.router.routes:
        m, _ = r.matches(scope)
        if m == Match.FULL:
            return getattr(r, "path", None)
    return None


_orig_request = TestClient.request


def _recording_request(self, method, url, *args, **kwargs):
    resp = _orig_request(self, method, url, *args, **kwargs)
    m = method.upper()
    if m in ("POST", "PUT", "PATCH", "DELETE") and 200 <= resp.status_code < 300:
        route = _route_of(self.app, m, str(url).replace(str(self.base_url), ""))
        if route:
            _CALLED.add((m, route))
    if not _ALL_WRITES:
        for r in self.app.router.routes:
            for mm in (getattr(r, "methods", None) or set()) & {"POST", "PUT", "PATCH", "DELETE"}:
                if getattr(r, "path", "").startswith("/api/"):
                    _ALL_WRITES.add((mm, r.path))
    return resp


TestClient.request = _recording_request


def pytest_sessionfinish(session, exitstatus):
    """Enforced only on a full, unfiltered run of tests/ (not with -k or one file)."""
    opt = session.config.option
    full = not opt.keyword and not opt.markexpr and not getattr(opt, "lf", False) \
        and all(os.path.basename(str(a).rstrip("/\\")) in ("tests", "") for a in (session.config.args or ["tests"]))
    report = os.environ.get("AXAR_INPUT_COVERAGE_REPORT")
    missing = sorted(_ALL_WRITES - _CALLED)
    if report:
        with open(report, "w", encoding="utf-8") as fh:
            fh.write("\n".join(f"{m} {p}" for m, p in missing))
    if full and _ALL_WRITES and missing and exitstatus == 0:
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr:
            tr.write_line(f"INPUT PATHS NEVER EXERCISED WITH SUCCESS ({len(missing)}): "
                          + ", ".join(f"{m} {p}" for m, p in missing), red=True)
        session.exitstatus = 1
