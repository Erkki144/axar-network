from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from axar_pos.api import create_app
from axar_pos.config import Settings
from axar_pos.models import Location, Product, Register, Tenant


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def clock() -> Clock:
    # 2026-03-10 10:00 UTC == 12:00 Europe/Tallinn (EET, UTC+2)
    return Clock(datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc))


@pytest.fixture
def settings(clock, tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'pos.db'}",
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
        ])
    return app


class Api:
    """Thin helper that always sends a tenant identity."""

    def __init__(self, client: TestClient, tenant: str, cashier: str = "kassa1"):
        self.c = client
        self.h = {"X-Tenant-ID": tenant, "X-Cashier-ID": cashier}

    def get(self, path, **kw):
        return self.c.get(path, headers=self.h, **kw)

    def post(self, path, json=None, headers=None):
        return self.c.post(path, json=json, headers={**self.h, **(headers or {})})

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


@pytest.fixture
def a(client) -> Api:
    return Api(client, "t-a")


@pytest.fixture
def b(client) -> Api:
    return Api(client, "t-b")
