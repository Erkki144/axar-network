"""#14 vertical slice: goods receipt -> costing -> recipe -> sale -> COGS ->
margin -> reconciliation. Runs on SQLite and PostgreSQL (AXAR_TEST_PG)."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from axar_pos import worker
from axar_pos.models import AppendOnlyViolation, Product, StockMovement

KG = 1_000_000          # micro units per kg / per piece
GROSS_10_NET = 1240     # €10.00 net + 24 % VAT


@pytest.fixture
def inv(a, app, settings):
    """Tenant A with a default warehouse at R1's location, an ingredient, a
    supplier and the menu offer SUPP (€10 net)."""
    m = a.as_role("manager")
    with app.state.session_factory() as s:
        s.add_all([Product(tenant_id="t-a", sku="SUPP", name="Kartulisupp", price_cents=GROSS_10_NET),
                   Product(tenant_id="t-a", sku="SUPP2", name="Supp (partii)", price_cents=GROSS_10_NET)])
        s.commit()
    loc = next(r for r in a.get("/api/v1/registers").json() if r["code"] == "R1")["location"]["id"]
    wh = m.post("/api/v1/inventory/warehouses", {"location_id": loc, "code": "W1", "name": "Ladu", "is_default": True})
    pot = m.post("/api/v1/inventory/items", {"sku": "POTATO", "name": "Kartul", "kind": "ingredient", "uom": "kg"})
    sup = m.post("/api/v1/inventory/suppliers", {"code": "FARM", "name": "Talu OÜ"})
    assert wh.status_code == pot.status_code == sup.status_code == 201
    return {"m": m, "wh": wh.json()["id"], "potato": pot.json()["id"], "sup": sup.json()["id"],
            "supp": a.product_id("SUPP"), "supp2": a.product_id("SUPP2"), "settings": settings, "app": app}


def deliver(inv, doc_no="SL-1", qty=10 * KG, net=5000, vat=2400, extra_lines=(), freight=0, doc_type="delivery_note",
            matches=None):
    body = {"supplier_id": inv["sup"], "doc_type": doc_type, "doc_no": doc_no, "doc_date": "2026-03-10",
            "warehouse_id": inv["wh"], "freight_cents": freight, "matches_document_id": matches,
            "lines": [{"item_id": inv["potato"], "qty_micro": qty, "net_cents": net, "vat_rate_bp": vat},
                      *extra_lines]}
    r = inv["m"].post("/api/v1/inventory/documents", body)
    assert r.status_code == 201, r.text
    p = inv["m"].post(f"/api/v1/inventory/documents/{r.json()['id']}/post")
    assert p.status_code == 200, p.text
    return r.json()["id"]


def recipe(inv, per_portion=200_000, product="supp"):
    r = inv["m"].post("/api/v1/inventory/recipes", {"product_id": inv[product], "valid_from": "2026-01-01",
                                                    "lines": [{"item_id": inv["potato"], "qty_micro": per_portion}]})
    assert r.status_code == 201, r.text


def stock_of(inv, item):
    return next(x for x in inv["m"].get("/api/v1/inventory/stock").json() if x["item_id"] == item)


def run_worker(inv):
    worker.run_once(inv["app"].state.request_factory, inv["settings"], "test-worker")


def recon(inv):
    r = inv["m"].get("/api/v1/inventory/reconciliation").json()
    assert r["ok"], r
    return r


# ------------------------------------------------------------- acceptance


def test_owner_acceptance_receive_recipe_sell_margin(a, inv):
    """10 kg at €5/kg net; 0.2 kg per portion; 20 portions at €10 net ->
    6 kg and €30 stock, €20 COGS, €200 net revenue, €180 ingredient margin."""
    deliver(inv)
    recipe(inv)
    a.open_drawer()
    sale = a.sell([("SUPP", 20)], 20 * GROSS_10_NET)
    assert sale.status_code == 201 and sale.json()["net_cents"] == 20000
    assert stock_of(inv, inv["potato"])["qty_micro"] == 10 * KG       # nothing consumed before the worker runs
    run_worker(inv)
    st = stock_of(inv, inv["potato"])
    assert (st["qty_micro"], st["value_cents"]) == (6 * KG, 3000)
    m = inv["m"].get("/api/v1/inventory/margin?business_date=2026-03-10").json()
    assert (m["net_revenue_cents"], m["cogs_cents"], m["gross_margin_cents"]) == (20000, 2000, 18000)
    run_worker(inv)                                                     # redelivery: no second consumption
    assert stock_of(inv, inv["potato"])["qty_micro"] == 6 * KG
    r = recon(inv)
    assert (r["stock_value_cents"], r["cogs_cents"]) == (3000, 2000)


def test_late_invoice_price_difference_split_between_stock_and_cogs(a, inv):
    note = deliver(inv)
    recipe(inv)
    a.open_drawer()
    a.sell([("SUPP", 20)], 20 * GROSS_10_NET)
    run_worker(inv)
    deliver(inv, doc_no="ARVE-77", net=5500, doc_type="invoice", matches=note)   # €5.50/kg invoiced
    st = stock_of(inv, inv["potato"])
    assert (st["qty_micro"], st["value_cents"]) == (6 * KG, 3300)               # +€3 on the 6 kg left
    assert recon(inv)["cogs_cents"] == 2200                                     # +€2 on the 4 kg consumed
    kinds = [x["kind"] for x in inv["m"].get(f"/api/v1/inventory/items/{inv['potato']}/card").json()]
    assert kinds == ["receipt", "sale_consumption", "invoice_adjustment", "cogs_adjustment"]  # history kept


def test_duplicate_document_and_double_post_are_refused(inv):
    doc = deliver(inv)
    body = {"supplier_id": inv["sup"], "doc_type": "delivery_note", "doc_no": "SL-1", "doc_date": "2026-03-10",
            "warehouse_id": inv["wh"], "lines": [{"item_id": inv["potato"], "qty_micro": KG, "net_cents": 500,
                                                  "vat_rate_bp": 2400}]}
    r = inv["m"].post("/api/v1/inventory/documents", body)
    assert r.status_code == 409 and r.json()["error"] == "duplicate_document"
    r = inv["m"].post(f"/api/v1/inventory/documents/{doc}/post")
    assert r.status_code == 409 and r.json()["error"] == "already_posted"
    assert stock_of(inv, inv["potato"])["qty_micro"] == 10 * KG


def test_partial_return_to_supplier_at_average_cost(inv):
    doc = deliver(inv)
    r = inv["m"].post(f"/api/v1/inventory/documents/{doc}/returns",
                      {"item_id": inv["potato"], "qty_micro": KG, "return_no": "TAG-1"})
    assert r.status_code == 201 and r.json() == {"qty_micro": -KG, "value_cents": -500}
    st = stock_of(inv, inv["potato"])
    assert (st["qty_micro"], st["value_cents"]) == (9 * KG, 4500)
    r = inv["m"].post(f"/api/v1/inventory/documents/{doc}/returns",
                      {"item_id": inv["potato"], "qty_micro": 10 * KG, "return_no": "TAG-2"})
    assert r.status_code == 422                                                # more than received
    assert recon(inv)["cogs_cents"] == 0                                       # a return is not COGS


def test_batch_production_then_sale_never_double_deducts(a, inv):
    """Batch alternative: 2 kg potato -> 10 soup portions; SUPP2 sells the
    finished portion, so the sale consumes portions, not potatoes."""
    m = inv["m"]
    deliver(inv)
    soup = m.post("/api/v1/inventory/items", {"sku": "SOUP-P", "name": "Supp portsjon", "kind": "finished",
                                              "uom": "pcs"}).json()["id"]
    assert m.post("/api/v1/inventory/recipes", {"output_item_id": soup, "valid_from": "2026-01-01",
                  "yield_micro": 10 * KG, "lines": [{"item_id": inv["potato"], "qty_micro": 2 * KG}]}).status_code == 201
    key = {"Idempotency-Key": "batch-0001"}
    b1 = m.post("/api/v1/inventory/production", {"output_item_id": soup, "warehouse_id": inv["wh"], "batches": 1}, key)
    b2 = m.post("/api/v1/inventory/production", {"output_item_id": soup, "warehouse_id": inv["wh"], "batches": 1}, key)
    assert b1.json() == b2.json() and b1.json()["cost_cents"] == 1000           # idempotent
    assert m.post("/api/v1/inventory/sell-item", {"product_id": inv["supp2"], "item_id": soup}).status_code == 200
    r = m.post("/api/v1/inventory/recipes", {"product_id": inv["supp2"], "valid_from": "2026-01-01",
                                             "lines": [{"item_id": inv["potato"], "qty_micro": 200_000}]})
    assert r.status_code == 422                                                 # cannot ALSO consume a recipe
    a.open_drawer()
    a.sell([("SUPP2", 4)], 4 * GROSS_10_NET)
    run_worker(inv)
    potato, portion = stock_of(inv, inv["potato"]), stock_of(inv, soup)
    assert (potato["qty_micro"], potato["value_cents"]) == (8 * KG, 4000)      # only the production consumed potatoes
    assert (portion["qty_micro"], portion["value_cents"]) == (6 * KG, 600)
    assert recon(inv)["cogs_cents"] == 400


def test_mixed_vat_nonrecoverable_vat_and_freight_allocation(inv):
    m = inv["m"]
    oil = m.post("/api/v1/inventory/items", {"sku": "OIL", "name": "Õli", "kind": "ingredient", "uom": "l"}).json()["id"]
    deliver(inv, doc_no="SL-9", net=1001, vat=900, freight=100,
            extra_lines=[{"item_id": oil, "qty_micro": 2 * KG, "net_cents": 2002, "vat_rate_bp": 2400,
                          "vat_recoverable": False}])
    pot, o = stock_of(inv, inv["potato"]), stock_of(inv, oil)
    # potato: 10.01 net (+9 % VAT recoverable -> not in cost) + 1/3 of freight
    # oil: 20.02 net + 24 % non-recoverable VAT (4.80) + 2/3 of freight
    assert pot["value_cents"] + o["value_cents"] == 1001 + 2002 + 480 + 100     # freight allocated exactly
    assert o["value_cents"] == 2002 + 480 + 67 and pot["value_cents"] == 1001 + 33


def test_cost_rounding_never_leaves_residue(inv):
    deliver(inv, qty=3 * KG, net=1000)                       # €3.333…/kg
    soup = inv["m"].post("/api/v1/inventory/items", {"sku": "X", "name": "X", "kind": "finished",
                                                     "uom": "pcs"}).json()["id"]
    inv["m"].post("/api/v1/inventory/recipes", {"output_item_id": soup, "valid_from": "2026-01-01",
                                                "yield_micro": KG, "lines": [{"item_id": inv["potato"], "qty_micro": KG}]})
    costs = [inv["m"].post("/api/v1/inventory/production", {"output_item_id": soup, "warehouse_id": inv["wh"],
                                                             "batches": 1},
                           {"Idempotency-Key": f"round-{i}-0001"}).json()["cost_cents"] for i in range(3)]
    assert sum(costs) == 1000 and stock_of(inv, inv["potato"])["value_cents"] == 0
    recon(inv)


def test_negative_stock_is_allowed_but_flagged(a, inv):
    deliver(inv, qty=KG, net=500)
    recipe(inv, per_portion=600_000)
    a.open_drawer()
    a.sell([("SUPP", 2)], 2 * GROSS_10_NET)                  # needs 1.2 kg, 1 kg on hand
    run_worker(inv)
    st = stock_of(inv, inv["potato"])
    assert st["qty_micro"] == -200_000 and st["negative"]
    r = recon(inv)
    assert r["negative_stock"] and r["cogs_cents"] == 500 + 100   # the missing 0.2 kg at last cost €5/kg


def test_failed_operations_write_nothing(a, inv):
    body = {"supplier_id": inv["sup"], "doc_type": "delivery_note", "doc_no": "BAD", "doc_date": "2026-03-10",
            "warehouse_id": inv["wh"], "lines": [{"item_id": 99999, "qty_micro": KG, "net_cents": 1, "vat_rate_bp": 0}]}
    assert inv["m"].post("/api/v1/inventory/documents", body).status_code == 404
    recipe(inv)
    a.open_drawer()
    assert a.sell([("SUPP", 1)], 1).status_code == 422       # insufficient tender: no sale, no event
    run_worker(inv)
    assert inv["m"].get("/api/v1/inventory/stock").json() == []
    r = recon(inv)
    assert r["cogs_cents"] == 0 and r["stock_value_cents"] == 0


def test_sale_without_default_warehouse_blocks_consumer_instead_of_skipping(a, inv, app):
    from axar_pos.models import EventConsumer, Warehouse

    deliver(inv)
    recipe(inv)
    with app.state.session_factory() as s:
        s.get(Warehouse, inv["wh"]).is_default = False
        s.commit()
    a.open_drawer()
    a.sell([("SUPP", 1)], GROSS_10_NET)
    from axar_pos import events
    for _ in range(events.MAX_ATTEMPTS + 1):
        run_worker(inv)
        inv["settings"].clock.at += events.BACKOFF_MAX
    with app.state.session_factory() as s:
        c = s.get(EventConsumer, ("stock.consume", "t-a"))
        assert c.blocked_at is not None and "InvalidInventory" in c.last_error
    assert stock_of(inv, inv["potato"])["qty_micro"] == 10 * KG   # nothing consumed, nothing skipped


def test_concurrent_sales_and_workers_consume_exactly(a, inv):
    deliver(inv)
    recipe(inv)
    a.open_drawer()
    with ThreadPoolExecutor(10) as pool:
        rs = list(pool.map(lambda _: a.sell([("SUPP", 1)], GROSS_10_NET), range(10)))
    assert all(r.status_code == 201 for r in rs)
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda i: worker.run_once(inv["app"].state.request_factory, inv["settings"], f"w{i}"), range(3)))
    run_worker(inv)
    st = stock_of(inv, inv["potato"])
    assert (st["qty_micro"], st["value_cents"]) == (8 * KG, 4000)
    assert recon(inv)["cogs_cents"] == 1000


def test_inventory_is_tenant_scoped_and_needs_manager(a, b, inv):
    deliver(inv)
    assert b.as_role("manager").get("/api/v1/inventory/stock").json() == []
    assert b.as_role("manager").get(f"/api/v1/inventory/items/{inv['potato']}/card").status_code == 404


def test_stock_movements_are_append_only(inv, app):
    deliver(inv)
    with app.state.session_factory() as s:
        mv = s.scalar(select(StockMovement))
        mv.value_cents = 1
        with pytest.raises(AppendOnlyViolation):
            s.flush()


def test_parallel_receipts_and_production_lose_no_update(inv):
    """Direct concurrent writers on the same balance row (no consumer in between):
    10 delivery notes posted and 5 batches produced in parallel."""
    m = inv["m"]
    deliver(inv, doc_no="BASE", qty=20 * KG, net=10000)
    soup = m.post("/api/v1/inventory/items", {"sku": "P", "name": "P", "kind": "finished", "uom": "pcs"}).json()["id"]
    m.post("/api/v1/inventory/recipes", {"output_item_id": soup, "valid_from": "2026-01-01", "yield_micro": KG,
                                         "lines": [{"item_id": inv["potato"], "qty_micro": KG}]})
    docs = []
    for i in range(10):
        r = m.post("/api/v1/inventory/documents", {
            "supplier_id": inv["sup"], "doc_type": "delivery_note", "doc_no": f"P-{i}", "doc_date": "2026-03-10",
            "warehouse_id": inv["wh"], "lines": [{"item_id": inv["potato"], "qty_micro": KG, "net_cents": 500,
                                                  "vat_rate_bp": 2400}]})
        docs.append(r.json()["id"])

    def post(d):
        return m.post(f"/api/v1/inventory/documents/{d}/post").status_code

    def produce(i):
        return m.post("/api/v1/inventory/production", {"output_item_id": soup, "warehouse_id": inv["wh"],
                                                       "batches": 1}, {"Idempotency-Key": f"par-{i}-0001"}).status_code

    with ThreadPoolExecutor(15) as pool:
        codes = list(pool.map(post, docs)) + list(pool.map(produce, range(5)))
    assert set(codes) <= {200, 201}, codes
    pot, p = stock_of(inv, inv["potato"]), stock_of(inv, soup)
    assert (pot["qty_micro"], pot["value_cents"]) == (25 * KG, 12500)   # 20 + 10 - 5 kg at €5
    assert (p["qty_micro"], p["value_cents"]) == (5 * KG, 2500)
    recon(inv)
