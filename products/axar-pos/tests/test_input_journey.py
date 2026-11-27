"""Every input, followed to where it must land (owner request 27.11.2026).

Builds on the #60 business day (`test_e2e_chain_reconciliation.day`: purchase,
recipe, two tills, table + kitchen, split payment, transfer + bank import,
refund) and adds the inputs that came later:

    office  : product group, new product, base price (proposed by one person,
              approved by another), outlet price list for a second outlet,
              recipe for the new product
    till    : the new product sold at outlet A (base price) and outlet B
              (list price), a safe drop, drawer close with the counted cash
    worker  : event consumers (stock + accounting queue)

For each input the test asserts the registers it must reach (sale lines,
stock movements, export queue, outbox events, drawer totals, price versions)
and that the whole day reconciles: R1-R14 green, only the honest R8 warning
(no accounting delivery adapter yet).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from test_catalog_pricing import approve, mgr, outlet_id, propose, till, two_outlets  # noqa: F401
from test_e2e_chain_reconciliation import by_check, day, recon  # noqa: F401
from test_inventory import KG, run_worker

from axar_pos.models import ExportQueue, OutboxEvent, PriceVersion, SaleLine, StockMovement

MARI, JAAN = "mari", "jaan"


def count(app, model, *where):
    with app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


@pytest.fixture
def journey(day, app, two_outlets):  # noqa: F811
    a, m, inv = day["a"], day["m"], day["inv"]
    out = {"day": day}

    # ---- office: group + new product + four-eyes base price -------------------
    g = mgr(a, MARI).post("/api/v1/catalog/groups", {"name": "Supid"})
    assert g.status_code == 201, g.text
    created = mgr(a, MARI).post("/api/v1/catalog/products", {
        "sku": "SELJ", "name": "Seljanka", "base_price_cents": 650, "group_id": g.json()["id"]})
    assert created.status_code == 201, created.text
    selj = created.json()["product"]["id"]
    assert "SELJ" not in till(a, "R1")                                    # not sold before approval
    assert approve(a, created.json()["price_draft"]["id"]).status_code == 200
    out["selj"] = selj

    # ---- office: second outlet gets its own price list ------------------------
    pl = mgr(a, MARI).post("/api/v1/pricing/lists", {"name": "Sadam"}).json()
    assert mgr(a, JAAN).put(f"/api/v1/pricing/outlets/{outlet_id(a, 'L2')}",
                            {"price_list_id": pl["id"]}).status_code == 200
    assert approve(a, propose(a, selj, 690, price_list_id=pl["id"])["id"]).status_code == 200

    # ---- office: the new product's recipe and outlet B's warehouse -------------
    r = m.post("/api/v1/inventory/recipes", {"product_id": selj, "valid_from": "2026-01-01",
                                             "lines": [{"item_id": inv["potato"], "qty_micro": 300_000}]})
    assert r.status_code == 201, r.text
    l2 = outlet_id(a, "L2")
    w2 = m.post("/api/v1/inventory/warehouses", {"location_id": l2, "code": "W2", "name": "Sadama ladu",
                                                 "is_default": True})
    assert w2.status_code == 201, w2.text
    out["w2"] = w2.json()["id"]

    # ---- till: sell the new product at both outlets ----------------------------
    a.open_drawer(3000, "R3")
    s_a = a.sell([("SELJ", 2)], 1300, register="R1")
    s_b = a.sell([("SELJ", 1)], 700, register="R3")
    assert (s_a.status_code, s_b.status_code) == (201, 201), (s_a.text, s_b.text)
    out["sale_a"], out["sale_b"] = s_a.json(), s_b.json()

    # ---- till: safe drop from R1, then close it with exactly the expected cash --
    d1 = day["d1"]["id"]
    drop = a.post(f"/api/v1/drawer/{d1}/movements", {"direction": "out", "amount_cents": 2000,
                                                      "reason_code": "SAFE_DROP", "reason_text": "Seifi üleandmine"},
                  headers={"Idempotency-Key": str(uuid.uuid4())})
    assert drop.status_code == 201, drop.text
    out["drop"] = drop.json()
    expected = a.drawer("R1")["expected_cents"]
    closed = a.post(f"/api/v1/drawer/{d1}/close", {"counted_cents": expected})
    assert closed.status_code == 200 and closed.json()["variance_cents"] == 0, closed.text

    run_worker(inv)
    run_worker(inv)                                                       # redelivery changes nothing
    return out


def test_new_product_price_lands_on_each_outlets_sale_lines(journey, app):
    a_line, b_line = journey["sale_a"]["lines"][0], journey["sale_b"]["lines"][0]
    assert (a_line["unit_price_cents"], a_line["gross_cents"]) == (650, 1300)     # outlet A: base price
    assert (b_line["unit_price_cents"], b_line["gross_cents"]) == (690, 690)      # outlet B: list price
    assert count(app, PriceVersion, PriceVersion.product_id == journey["selj"],
                 PriceVersion.status == "approved") == 2                           # base + list, both approved


def test_new_product_sales_consume_stock_from_each_outlets_warehouse(journey, app):
    inv = journey["day"]["inv"]
    moves = {}
    with app.state.session_factory() as s:
        for sale in (journey["sale_a"], journey["sale_b"]):
            moves[sale["id"]] = [(mv.warehouse_id, mv.item_id, mv.qty_micro) for mv in s.scalars(
                select(StockMovement).where(StockMovement.ref_id == sale["id"]))]
    assert moves[journey["sale_a"]["id"]] == [(inv["wh"], inv["potato"], -2 * 300_000)]
    assert moves[journey["sale_b"]["id"]] == [(journey["w2"], inv["potato"], -300_000)]


def test_every_sale_and_refund_reaches_the_accounting_queue_exactly_once(journey, app):
    with app.state.session_factory() as s:
        sales = [x for (x,) in s.execute(select(OutboxEvent.aggregate_id).where(
            OutboxEvent.type.in_(("pos.sale.completed", "pos.refund.completed"))))]
        queued = [x for (x,) in s.execute(select(ExportQueue.event_id))]
    assert len(sales) == 6                     # 3 day sales + 2 new + 1 refund
    assert len(queued) == len(set(queued)) == 6


def test_safe_drop_lands_in_cash_custody_not_in_revenue(journey, app):
    drop = journey["drop"]
    assert drop["direction"] == "out" and drop["amount_cents"] == 2000
    assert count(app, OutboxEvent, OutboxEvent.type == "drawer.cash.moved") == 1
    assert count(app, ExportQueue, ExportQueue.event_type == "drawer.cash.moved") == 0   # K4: not in books yet
    assert count(app, SaleLine, SaleLine.name == "Seifi üleandmine") == 0


def test_the_whole_day_reconciles_r1_to_r14(journey):
    rep = recon(journey["day"]["m"])
    checks = by_check(rep)
    assert rep["ok"] is True, {k: c["problems"] for k, c in checks.items() if c["status"] == "fail"}
    # Two honest warnings, nothing wrong:
    # - R8: no accounting delivery adapter yet (P1-3);
    # - R10: outlet B sold soup, but potatoes were never moved to ITS warehouse,
    #   so W2 is negative. The system names the exact warehouse and item; the
    #   business fix is a stock transfer (P1-7), which does not exist yet.
    assert {k: c["status"] for k, c in checks.items() if c["status"] != "ok"} == {
        "R8.books_export": "warning", "R10.stock_ledger": "warning"}
    neg = checks["R10.stock_ledger"]["pending"]
    inv = journey["day"]["inv"]
    assert len(neg) == 1 and neg[0]["reason"] == "negative stock"
    assert (neg[0].get("warehouse_id"), neg[0].get("item_id")) == (journey["w2"], inv["potato"]), neg
    assert checks["R14.sale_prices"]["problem_count"] == 0 and checks["R14.sale_prices"]["checked"] >= 7
    assert checks["R1.drawer_cash"]["checked"] == 3


def test_trace_follows_a_new_product_sale_through_every_register(journey):
    m = journey["day"]["m"]
    t = m.get(f"/api/v1/admin/trace/sales/{journey['sale_b']['id']}").json()
    consumers = {c["consumer"] for e in t["events"] for c in e["applied_by"]}
    assert {"books.export", "stock.consume"} <= consumers
    assert len(t["stock_movements"]) == 1 and len(t["accounting_export"]) == 1


def test_a_sale_at_an_outlet_without_a_warehouse_is_named_not_hidden_as_lag(day, two_outlets):  # noqa: F811
    """Found 27.11.2026 while walking every path: outlet B has no warehouse.
    The sale itself succeeds, but stock consumption cannot run and every later
    sale queues behind it (also outlet A's). R7 must say WHY, not just "lag".
    Prevention is the outlet readiness check (P1-10)."""
    a, m, inv = day["a"], day["m"], day["inv"]
    c = mgr(a, MARI).post("/api/v1/catalog/products", {"sku": "SELJ", "name": "Seljanka",
                                                        "base_price_cents": 650}).json()
    approve(a, c["price_draft"]["id"])
    m.post("/api/v1/inventory/recipes", {"product_id": c["product"]["id"], "valid_from": "2026-01-01",
                                         "lines": [{"item_id": inv["potato"], "qty_micro": 300_000}]})
    a.open_drawer(3000, "R3")
    assert a.sell([("SELJ", 1)], 650, register="R3").status_code == 201
    run_worker(inv)
    r7 = by_check(recon(m))["R7.event_pipeline"]
    retrying = [x for x in r7["pending"] if x.get("reason") == "retrying"]
    assert retrying and retrying[0]["consumer"] == "stock.consume"
    assert retrying[0]["error"].startswith("InvalidInventory")  # messages are stored as type + fingerprint only
