"""P1-1a: product groups, product card, outlet price lists, four-eyes prices, R14.

Scope: docs/discovery/catalog.md §8 (owner answers 26.11.2026):
- prices may differ per outlet; the same product can have another price or
  not be sold at an outlet;
- a price change needs a second person: the proposer can never approve;
- deactivating a product never touches stock;
- past sales never change; R14 proves every sold price was valid then.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from axar_pos.models import Location, OutboxEvent, Register, SaleLine, StockMovement
from test_event_contracts import validator

MARI = "mari"   # proposes
JAAN = "jaan"   # approves


@pytest.fixture
def two_outlets(app):
    """t-a gets a second outlet L2 with register R3."""
    with app.state.session_factory.begin() as s:
        l2 = Location(tenant_id="t-a", code="L2", name="Kohvik A sadam")
        s.add(l2)
        s.flush()
        s.add(Register(tenant_id="t-a", location_id=l2.id, code="R3", name="Sadama kassa"))
    return app


def mgr(a, who):
    return a.as_role("manager", cashier=who)


def outlet_id(a, code):
    return next(o["id"] for o in mgr(a, MARI).get("/api/v1/pricing/outlets").json() if o["code"] == code)


def till(a, register="R1"):
    return {p["sku"]: p for p in a.get(f"/api/v1/products?register_id={a.register_id(register)}").json()}


def new_product(a, sku="SELJ", name="Seljanka", price=650, **kw):
    r = mgr(a, MARI).post("/api/v1/catalog/products", {"sku": sku, "name": name, "base_price_cents": price, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def approve(a, version_id, who=JAAN):
    return mgr(a, who).post(f"/api/v1/pricing/prices/{version_id}/approve", {})


def propose(a, product_id, price, price_list_id=None, who=MARI, **kw):
    r = mgr(a, who).post("/api/v1/pricing/prices", {"product_id": product_id, "price_list_id": price_list_id,
                                                    "price_cents": price, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def recon(app, tenant="t-a"):
    from axar_pos import reconcile

    with app.state.session_factory() as s:
        return {c["check"]: c for c in reconcile.check(s, tenant)["checks"]}


# ------------------------------------------------------------------ four eyes


def test_new_product_is_sold_only_after_a_second_person_approves_its_price(a):
    created = new_product(a)
    draft = created["price_draft"]
    assert draft["status"] == "draft" and draft["created_by"] == MARI
    assert "SELJ" not in till(a)                                  # no approved price yet: sold nowhere

    r = approve(a, draft["id"], who=MARI)                         # the proposer tries to approve
    assert r.status_code == 403 and "four eyes" in r.json()["message"]
    assert "SELJ" not in till(a)

    r = approve(a, draft["id"])
    assert r.status_code == 200 and r.json()["decided_by"] == JAAN
    assert till(a)["SELJ"]["price_cents"] == 650

    a.open_drawer()
    sale = a.sell([("SELJ", 2)], 1300)
    assert sale.status_code == 201 and sale.json()["total_cents"] == 1300
    assert recon(a.c.app)["R14.sale_prices"]["status"] == "ok"


def test_a_decided_price_cannot_be_decided_again_and_only_the_proposer_withdraws(a):
    v = new_product(a)["price_draft"]
    assert mgr(a, JAAN).post(f"/api/v1/pricing/prices/{v['id']}/withdraw", {}).status_code == 403
    assert mgr(a, MARI).post(f"/api/v1/pricing/prices/{v['id']}/withdraw", {}).json()["status"] == "withdrawn"
    assert approve(a, v["id"]).status_code == 409
    assert "SELJ" not in till(a)

    pid = a.product_id("KOHV")
    v2 = propose(a, pid, 300)
    r = mgr(a, JAAN).post(f"/api/v1/pricing/prices/{v2['id']}/reject", {"note": "liiga kallis"})
    assert r.json()["status"] == "rejected" and r.json()["decision_note"] == "liiga kallis"
    assert till(a)["KOHV"]["price_cents"] == 250                  # rejected: nothing changed


# ------------------------------------------------------------------ history


def test_price_change_keeps_history_and_a_pending_draft_does_not_hide_the_product(a, clock):
    a.open_drawer()
    old = a.sell([("KOHV", 1)], 250).json()
    pid = a.product_id("KOHV")

    v = propose(a, pid, 270, reason="kohvioad kallinesid")
    assert till(a)["KOHV"]["price_cents"] == 250                  # draft: the till keeps selling at 250

    clock.at += timedelta(minutes=5)
    assert approve(a, v["id"]).status_code == 200
    assert till(a)["KOHV"]["price_cents"] == 270
    new = a.sell([("KOHV", 1)], 270).json()
    assert new["lines"][0]["unit_price_cents"] == 270
    assert a.get(f"/api/v1/sales/{old['id']}").json()["lines"][0]["unit_price_cents"] == 250

    card = mgr(a, MARI).get(f"/api/v1/catalog/products/{pid}").json()
    assert [h["price_cents"] for h in card["history"]][:2] == [270, 250]   # legacy price written down
    assert recon(a.c.app)["R14.sale_prices"]["status"] == "ok"            # both sales match their moment


def test_future_price_takes_effect_only_from_its_moment(a, clock):
    pid = a.product_id("SAI")
    start = clock.at + timedelta(days=1)
    v = propose(a, pid, 220, valid_from=start.isoformat())
    assert approve(a, v["id"]).status_code == 200
    assert till(a)["SAI"]["price_cents"] == 199
    clock.at = start + timedelta(seconds=1)
    assert till(a)["SAI"]["price_cents"] == 220


def test_an_approval_never_back_dates_a_price(a, clock):
    pid = a.product_id("SAI")
    v = propose(a, pid, 205, valid_from=(clock.at - timedelta(days=30)).isoformat())
    clock.at += timedelta(hours=1)
    out = approve(a, v["id"]).json()
    assert out["valid_from"] == clock.at.isoformat()


# ------------------------------------------------------------------ outlets


def test_same_product_different_price_per_outlet_and_not_sold_at_one(a, two_outlets):
    pl = mgr(a, MARI).post("/api/v1/pricing/lists", {"name": "Sadam"}).json()
    assert mgr(a, JAAN).put(f"/api/v1/pricing/outlets/{outlet_id(a, 'L2')}", {"price_list_id": pl["id"]}).status_code == 200

    kohv, raamat = a.product_id("KOHV"), a.product_id("RAAMAT")
    assert approve(a, propose(a, kohv, 290, price_list_id=pl["id"])["id"]).status_code == 200
    assert approve(a, propose(a, raamat, None, price_list_id=pl["id"])["id"]).status_code == 200  # not sold at L2

    assert till(a, "R1")["KOHV"]["price_cents"] == 250
    assert till(a, "R3")["KOHV"]["price_cents"] == 290
    assert "RAAMAT" in till(a, "R1") and "RAAMAT" not in till(a, "R3")
    assert till(a, "R3")["SAI"]["price_cents"] == 199              # no override: base price

    a.open_drawer(register="R1")
    a.open_drawer(register="R3")
    assert a.sell([("KOHV", 1)], 250, register="R1").json()["total_cents"] == 250
    assert a.sell([("KOHV", 1)], 290, register="R3").json()["total_cents"] == 290
    r = a.sell([("RAAMAT", 1)], 2000, register="R3")
    assert r.status_code == 409 and "not sold here" in r.json()["message"]

    q = a.post("/api/v1/sales/quote", {"register_id": a.register_id("R3"),
                                       "lines": [{"product_id": kohv, "quantity": 2}]}).json()
    assert q["total_cents"] == 580
    assert recon(a.c.app)["R14.sale_prices"]["status"] == "ok"


def test_preview_shows_each_outlet_before_and_after(a, two_outlets):
    pl = mgr(a, MARI).post("/api/v1/pricing/lists", {"name": "Sadam"}).json()
    mgr(a, JAAN).put(f"/api/v1/pricing/outlets/{outlet_id(a, 'L2')}", {"price_list_id": pl["id"]})
    v = propose(a, a.product_id("KOHV"), 260)                      # base price change
    pv = mgr(a, JAAN).get(f"/api/v1/pricing/prices/{v['id']}/preview").json()
    rows = {o["outlet"]: o for o in pv["outlets"]}
    assert rows["Kohvik A kesklinn"] == {**rows["Kohvik A kesklinn"], "before_cents": 250, "after_cents": 260,
                                          "affected": True}
    assert pv["vat_rate_bp"] == 2400


# ------------------------------------------------------------------ product card


def test_groups_tree_card_fields_and_deactivation_never_touch_stock(a, app):
    m = mgr(a, MARI)
    food = m.post("/api/v1/catalog/groups", {"name": "Toit"}).json()
    soups = m.post("/api/v1/catalog/groups", {"name": "Supid", "parent_id": food["id"]}).json()
    assert m.put(f"/api/v1/catalog/groups/{food['id']}", {"name": "Toit", "parent_id": soups["id"]}).status_code == 422

    p = new_product(a, group_id=soups["id"], short_name="Selj.", barcodes=["4740000000017"])["product"]
    approve(a, p["pending"][0]["id"])
    assert till(a)["SELJ"]["category"] == "Supid"
    assert m.post("/api/v1/catalog/products", {"sku": "X2", "name": "X", "base_price_cents": 1,
                                               "barcodes": ["4740000000017"]}).status_code == 409

    # an open bill holds it -> shown before deactivating
    table = next(t["id"] for t in a.get("/api/v1/tables").json() if t["code"] == "A1")
    order = a.post(f"/api/v1/tables/{table}/order").json()
    a.put(f"/api/v1/orders/{order['id']}/lines",
                            {"version": order["version"], "lines": [{"product_id": p["id"], "quantity": 1}]})
    assert m.get(f"/api/v1/catalog/products/{p['id']}/impact").json() == {"open_orders": 1}

    with app.state.session_factory() as s:
        moves_before = s.scalar(select(func.count(StockMovement.id)))
    r = m.put(f"/api/v1/catalog/products/{p['id']}", {"version": p["version"], "active": False})
    assert r.status_code == 200 and r.json()["active"] is False
    assert "SELJ" not in till(a)
    with app.state.session_factory() as s:
        assert s.scalar(select(func.count(StockMovement.id))) == moves_before
    stale = m.put(f"/api/v1/catalog/products/{p['id']}", {"version": p["version"], "name": "Vana"})
    assert stale.status_code == 409                                # optimistic lock

    assert m.put(f"/api/v1/catalog/groups/{soups['id']}", {"name": "Supid", "parent_id": food["id"],
                                                          "active": False}).status_code == 200  # no active products left


def test_group_with_active_products_cannot_be_deactivated(a):
    m = mgr(a, MARI)
    g = m.post("/api/v1/catalog/groups", {"name": "Joogid"}).json()
    new_product(a, sku="VESI", name="Vesi", price=180, group_id=g["id"])
    assert m.put(f"/api/v1/catalog/groups/{g['id']}", {"name": "Joogid", "active": False}).status_code == 409


def test_tenants_never_see_each_others_catalog(a, b):
    new_product(a)
    assert mgr(b, "x").get("/api/v1/catalog/products").json()[0]["sku"] == "B-SECRET"
    pid = a.product_id("KOHV")
    assert mgr(b, "x").get(f"/api/v1/catalog/products/{pid}").status_code == 404
    assert mgr(b, "x").post("/api/v1/pricing/prices", {"product_id": pid, "price_cents": 1}).status_code == 404


# ------------------------------------------------------------------ R14 + events


def test_r14_catches_a_price_changed_behind_the_application_and_broken_four_eyes(a, app):
    a.open_drawer()
    a.sell([("KOHV", 1)], 250)
    assert recon(app)["R14.sale_prices"]["status"] == "ok"
    with app.state.session_factory.begin() as s:   # someone edits the legacy price in the database
        s.execute(text("UPDATE products SET price_cents = 199 WHERE sku = 'KOHV' AND tenant_id = 't-a'"))
    f = recon(app)["R14.sale_prices"]
    assert f["status"] == "fail" and f["problems"][0]["charged_cents"] == 250

    with app.state.session_factory.begin() as s:
        s.execute(text("UPDATE products SET price_cents = 250 WHERE sku = 'KOHV' AND tenant_id = 't-a'"))
    v = propose(a, a.product_id("SAI"), 210)
    approve(a, v["id"])
    with app.state.session_factory.begin() as s:   # the approval is rewritten to the proposer
        s.execute(text("UPDATE price_versions SET decided_by = created_by WHERE id = :i"), {"i": v["id"]})
    f = recon(app)["R14.sale_prices"]
    assert f["status"] == "fail" and f["problems"][0]["reason"] == "four eyes broken"


def test_catalog_events_match_their_committed_schemas(a, app, two_outlets):
    m = mgr(a, MARI)
    g = m.post("/api/v1/catalog/groups", {"name": "Toit"}).json()
    p = new_product(a, group_id=g["id"])["product"]
    approve(a, p["pending"][0]["id"])
    m.put(f"/api/v1/catalog/products/{p['id']}", {"version": p["version"], "hidden_at_till": True})
    pl = m.post("/api/v1/pricing/lists", {"name": "Sadam"}).json()
    mgr(a, JAAN).put(f"/api/v1/pricing/outlets/{outlet_id(a, 'L2')}", {"price_list_id": pl["id"]})
    approve(a, propose(a, p["id"], None, price_list_id=pl["id"])["id"])

    with app.state.session_factory() as s:
        evs = list(s.scalars(select(OutboxEvent).where(OutboxEvent.tenant_id == "t-a")
                             .order_by(OutboxEvent.recorded_seq)))
    types = [e.type for e in evs]
    assert types == ["catalog.group.changed", "catalog.product.changed", "pricing.price.published",
                     "catalog.product.changed", "pricing.outlet.changed", "pricing.price.published"]
    for e in evs:
        errors = [x.message for x in validator(f"{e.type}.v{e.version}.schema.json").iter_errors(json.loads(e.payload))]
        assert errors == [], (e.type, errors)
    published = json.loads(evs[2].payload)
    assert published["proposed_by"] == MARI and published["approved_by"] == JAAN


def test_sale_line_snapshot_is_what_the_customer_paid(a, app):
    a.open_drawer()
    sid = a.sell([("RAAMAT", 1)], 1600).json()["id"]  # cash rounds to 5 cents
    approve(a, propose(a, a.product_id("RAAMAT"), 1799)["id"])
    with app.state.session_factory() as s:
        assert s.scalar(select(SaleLine.unit_price_cents).where(SaleLine.sale_id == sid)) == 1599
