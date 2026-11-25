from fastapi.testclient import TestClient

from axar_pos.api import create_app
from axar_pos.config import Settings


def test_products_are_tenant_scoped(a, b):
    assert {p["sku"] for p in a.get("/api/v1/products").json()} == {"KOHV", "SAI", "RAAMAT"}
    assert {p["sku"] for p in b.get("/api/v1/products").json()} == {"B-SECRET"}


def test_cannot_sell_another_tenants_product(a, b):
    a.open_drawer()
    b_product = b.product_id("B-SECRET")
    r = a.post("/api/v1/sales/cash",
               {"register_id": a.register_id(),
                "lines": [{"product_id": b_product, "quantity": 1}], "tendered_cents": 1000},
               headers={"Idempotency-Key": "cross-tenant-1"})
    assert r.status_code == 404  # indistinguishable from "does not exist"


def test_cannot_use_another_tenants_register(a, b):
    b.open_drawer()
    b_reg = b.register_id("R1")
    r = a.post("/api/v1/drawer/open", {"register_id": b_reg, "opening_float_cents": 0})
    assert r.status_code == 404
    r = a.post("/api/v1/sales/cash",
               {"register_id": b_reg,
                "lines": [{"product_id": a.product_id("KOHV"), "quantity": 1}],
                "tendered_cents": 250},
               headers={"Idempotency-Key": "cross-tenant-reg"})
    assert r.status_code == 404
    assert a.get(f"/api/v1/registers/{b_reg}/drawer").status_code == 404
    assert b.drawer()["sale_count"] == 0
    assert {r["code"] for r in a.get("/api/v1/registers").json()} == {"R1", "R2"}
    assert {r["code"] for r in b.get("/api/v1/registers").json()} == {"R1"}


def test_cannot_read_or_list_another_tenants_sales(a, b):
    a.open_drawer()
    sale = a.sell([("KOHV", 1)], 250).json()
    assert b.get(f"/api/v1/sales/{sale['id']}").status_code == 404
    assert b.get(f"/api/v1/sales/{sale['id']}/receipt.txt").status_code == 404
    assert b.get("/api/v1/sales").json() == []
    assert b.get("/api/v1/reports/daily", params={"business_date": "2026-03-10"}).json()["sale_count"] == 0


def test_cannot_close_another_tenants_drawer(a, b):
    d = a.open_drawer()
    r = b.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 0})
    assert r.status_code == 404
    assert b.get(f"/api/v1/drawer/{d['id']}").status_code == 404
    assert a.drawer()["status"] == "open"


def test_drawers_are_independent_per_tenant(a, b):
    a.open_drawer()
    b.open_drawer()  # would 409 if the open-drawer rule leaked across tenants


def test_idempotency_keys_are_per_tenant(a, b):
    a.open_drawer()
    b.open_drawer()
    ra = a.sell([("KOHV", 1)], 250, key="shared-key-0001")
    rb = b.sell([("B-SECRET", 1)], 1000, key="shared-key-0001")
    assert ra.status_code == 201 and rb.status_code == 201
    assert ra.json()["id"] != rb.json()["id"]
    assert rb.json()["receipt_no"] == 1


def test_missing_identity_headers(client):
    assert client.get("/api/v1/products").status_code == 401


def test_unknown_tenant(client):
    r = client.get("/api/v1/products", headers={"X-Tenant-ID": "nope", "X-Cashier-ID": "x"})
    assert r.status_code == 403


def test_no_implicit_demo_identity_by_default(tmp_path):
    # Settings() defaults demo_identity=False: tenant APIs must refuse.
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'p.db'}"))
    with TestClient(app) as c:
        r = c.get("/api/v1/products", headers={"X-Tenant-ID": "demo", "X-Cashier-ID": "x"})
        assert r.status_code == 503
        assert c.get("/health").status_code == 200
