"""#76: till (POS) and office are separate realms, enforced by the server.

With real sessions (no demo identity): a till token (PIN on an enrolled device)
cannot change the catalog, prices, stock, staff or settings; an office token
(password + TOTP) cannot sell, open bills, touch the kitchen, move drawer cash
or capture payments. Every refused attempt leaves the database unchanged.
The same person needs two separate logins; neither token becomes the other.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from test_auth import MGR_PW, open_drawer, settings, shop, world  # noqa: F401  (fixtures: real sessions)

from axar_pos import realms
from axar_pos.api import create_app
from axar_pos.config import Settings
from axar_pos.models import (DrawerCashMovement, Order, OutboxEvent, Payment, PriceVersion, Product,
                             ProductGroup, Sale, StockMovement)

TABLES = (Sale, Order, Payment, DrawerCashMovement, StockMovement, OutboxEvent, PriceVersion, Product, ProductGroup)


def snapshot(app):
    with app.state.session_factory() as s:
        return {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in TABLES}


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}


# ------------------------------------------------------------------ guard


def test_every_api_route_has_exactly_one_realm():
    app = create_app(Settings(database_url="sqlite://", demo_identity=True))
    missing = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/"):
            continue
        for m in (getattr(r, "methods", None) or set()) - {"HEAD"}:
            if realms.realm_of(m, path) is None:
                missing.append(f"{m} {path}")
    assert missing == [], f"routes without a realm (add them to axar_pos/realms.py): {missing}"


def test_unknown_route_is_refused_fail_closed():
    with pytest.raises(realms.WrongRealm):
        realms.check("POST", "/api/v1/something/new", "admin", demo=False)


# ------------------------------------------------------------------ till token in the office


def test_till_token_cannot_reach_any_office_action(shop, world):
    till, app = shop["till"], world.app
    before = snapshot(app)
    attempts = [
        ("post", "/api/v1/catalog/products", {"sku": "X", "name": "X", "base_price_cents": 100}),
        ("post", "/api/v1/catalog/groups", {"name": "Toit"}),
        ("post", "/api/v1/pricing/prices", {"product_id": 1, "price_cents": 1}),
        ("post", "/api/v1/pricing/lists", {"name": "Sadam"}),
        ("post", "/api/v1/inventory/items", {"sku": "K", "name": "Kartul", "kind": "ingredient", "uom": "kg"}),
        ("post", "/api/v1/admin/staff", {"display_name": "Uus", "pin": "5829",
                                         "roles": [{"role": "cashier", "location_id": shop["loc"]}]}),
        ("put", "/api/v1/admin/pos-settings", {"offline_limit_hours": 1}),
        ("get", "/api/v1/admin/reconciliation", None),
        ("get", "/api/v1/catalog/products", None),
        ("get", "/api/v1/inventory/stock", None),
        ("get", "/api/v1/admin/staff", None),
    ]
    for method, path, body in attempts:
        r = till.get(path) if method == "get" else getattr(till, method)(path, body)
        assert r.status_code == 403 and r.json()["error"] == "wrong_realm", (path, r.status_code, r.text)
    assert snapshot(app) == before


# ------------------------------------------------------------------ office token at the till


def test_office_token_cannot_sell_move_cash_or_touch_bills_and_kitchen(shop, world):
    adm, app, reg = shop["adm"], world.app, shop["reg"]
    d = open_drawer(shop["till"], reg)                    # a real open drawer exists
    before = snapshot(app)
    attempts = [
        ("/api/v1/sales/cash", {"register_id": reg, "tendered_cents": 1000,
                                "lines": [{"product_id": 1, "quantity": 1}]}),
        ("/api/v1/drawer/open", {"register_id": shop["w"].ids[("t-a", "L1", "R2")], "opening_float_cents": 0}),
        (f"/api/v1/drawer/{d['id']}/movements", {"direction": "out", "amount_cents": 100,
                                                  "reason_code": "SAFE_DROP", "reason_text": "Seifi üleandmine"}),
        (f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 0}),
        ("/api/v1/tables/1/order", None),
        ("/api/v1/kitchen/tickets/x/ready", None),
        ("/api/v1/orders/x/payments", {"method": "card", "amount_cents": 100, "register_id": reg}),
        ("/api/v1/sales/quote", {"lines": [{"product_id": 1, "quantity": 1}]}),
    ]
    for path, body in attempts:
        r = adm.post(path, body, key())
        assert r.status_code == 403 and r.json()["error"] == "wrong_realm", (path, r.status_code, r.text)
    for path in ("/api/v1/products", "/api/v1/registers", "/api/v1/tables", "/api/v1/kitchen/tickets"):
        r = adm.get(path)
        assert r.status_code == 403 and r.json()["error"] == "wrong_realm", path
    assert snapshot(app) == before
    assert shop["till"].get(f"/api/v1/registers/{reg}/drawer").json()["drawer"]["status"] == "open"


def test_both_realms_may_read_sales_history_and_the_daily_report(shop):
    open_drawer(shop["till"], shop["reg"])
    assert shop["till"].sell(shop["reg"]).status_code == 201
    for who in (shop["till"], shop["adm"]):
        assert who.get("/api/v1/sales").status_code == 200
    assert shop["adm"].get("/api/v1/reports/daily").status_code == 200
    # The realm allows reading; the cashier's ROLE still does not (reports = managers).
    r = shop["till"].get("/api/v1/reports/daily")
    assert r.status_code == 403 and r.json()["error"] == "forbidden"


def test_same_person_needs_two_logins_and_tokens_never_swap(shop, world):
    """A tenant-wide manager may work at the till AND in the office, but each
    needs its own login; logging out of one leaves the other untouched."""
    adm = shop["adm"]
    boss = world.staff(adm, "Liis", [("manager", None)], "6618")
    secret = adm.put(f"/api/v1/admin/staff/{boss}/credentials", {"login": "liis", "password": MGR_PW}).json()["totp_secret"]
    office = world.admin(login="liis", password=MGR_PW, secret=secret)
    till = world.till(shop["dev"], boss, "6618")
    assert office.get("/api/v1/auth/me").json()["kind"] == "admin"
    assert till.get("/api/v1/auth/me").json()["kind"] == "till"
    assert till.post("/api/v1/catalog/groups", {"name": "Toit"}).json()["error"] == "wrong_realm"
    assert office.get("/api/v1/registers").json()["error"] == "wrong_realm"
    assert office.post("/api/v1/catalog/groups", {"name": "Toit"}).status_code == 201
    assert till.get("/api/v1/registers").status_code == 200
    assert till.post("/api/v1/auth/logout").status_code == 204
    assert till.get("/api/v1/registers").status_code == 401           # till session ended
    assert office.get("/api/v1/catalog/groups").status_code == 200   # office session unaffected
