"""#60 end-to-end proof: one business day through every register, then the
cross-register consistency check (``/api/v1/admin/reconciliation``) and the
document trace (``/api/v1/admin/trace/sales/{id}``).

Chain: purchase (delivery note) -> stock -> recipe / stocked item ->
two registers -> express cash sale -> table bill -> kitchen ticket -> ready ->
split payment (simulated card + cash) -> transfer receivable -> bank import ->
partial refund -> event consumers -> stock consumption + accounting export
queue -> daily report -> drawer close.

Payments are SIMULATED (mock card terminal, no money moves). The accounting
export stays queued: there is no delivery adapter in main, and the check says
so as a warning instead of calling it done.

The tamper tests change one register behind the application's back and
prove the check names the broken invariant. Runs on SQLite and PostgreSQL.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from test_inventory import KG, deliver, recipe, run_worker
from test_offline_sync import central, edge  # noqa: F401  (fixtures)
from test_payments import bill, open_bill, pay

from axar_pos.models import Product

SUPP_GROSS = 1240      # €10.00 net + 24 % VAT


@pytest.fixture
def day(a, app, settings):
    """Office set-up (warehouse, items, supplier, recipe, stocked bun), then a
    full trading day on two registers. Returns ids for the assertions."""
    with app.state.session_factory() as s:
        s.add(Product(tenant_id="t-a", sku="SUPP", name="Kartulisupp", price_cents=SUPP_GROSS))
        s.commit()
    m = a.as_role("manager")
    loc = next(r for r in a.get("/api/v1/registers").json() if r["code"] == "R1")["location"]["id"]
    wh = m.post("/api/v1/inventory/warehouses", {"location_id": loc, "code": "W1", "name": "Ladu", "is_default": True})
    pot = m.post("/api/v1/inventory/items", {"sku": "POTATO", "name": "Kartul", "kind": "ingredient", "uom": "kg"})
    bun = m.post("/api/v1/inventory/items", {"sku": "BUN", "name": "Saiake (ost)", "kind": "resale", "uom": "pcs"})
    sup = m.post("/api/v1/inventory/suppliers", {"code": "FARM", "name": "Talu OÜ"})
    assert {wh.status_code, pot.status_code, bun.status_code, sup.status_code} == {201}, (wh.text, bun.text)
    inv = {"m": m, "wh": wh.json()["id"], "potato": pot.json()["id"], "sup": sup.json()["id"],
           "supp": a.product_id("SUPP"), "settings": settings, "app": app}
    deliver(inv, doc_no="SL-1", qty=10 * KG, net=5000, vat=2400,
            extra_lines=[{"item_id": bun.json()["id"], "qty_micro": 20 * KG, "net_cents": 2000, "vat_rate_bp": 2400}])
    recipe(inv, per_portion=200_000)                                          # 0.2 kg potato per soup
    r = m.post("/api/v1/inventory/sell-item", {"product_id": a.product_id("SAI"), "item_id": bun.json()["id"]})
    assert r.status_code == 200, r.text

    d1, d2 = a.open_drawer(5000, "R1"), a.open_drawer(2000, "R2")

    # Express till R1: 3 soups + 2 coffees, cash (a double tap re-sends the same key).
    key = str(uuid.uuid4())
    express = a.sell([("SUPP", 3), ("KOHV", 2)], 5000, key=key)
    assert express.status_code == 201, express.text
    again = a.sell([("SUPP", 3), ("KOHV", 2)], 5000, key=key)
    assert again.status_code == 200 and again.json()["id"] == express.json()["id"]

    # Table A1 on R2: order -> kitchen -> ready -> card + cash.
    o = open_bill(a, [("SUPP", 2), ("SAI", 4)])                               # 24.80 + 7.96 = 32.76
    k = a.post(f"/api/v1/orders/{o['id']}/kitchen", {"version": o["version"]},
               headers={"Idempotency-Key": str(uuid.uuid4())})
    assert k.status_code == 201, k.text
    ready = a.post(f"/api/v1/kitchen/tickets/{k.json()['ticket']['id']}/ready")
    assert ready.status_code == 200, ready.text
    total = bill(a, o["id"])["total_cents"]
    assert total == 2 * SUPP_GROSS + 4 * 199
    assert pay(a, o["id"], "card", 1000, total, register="R2", simulate="approved").status_code == 201
    cash = pay(a, o["id"], "cash", total - 1000, total, register="R2", tendered_cents=3000)
    assert cash.status_code == 201, cash.text
    table_sale = cash.json()["bill"]["sale_id"]
    assert table_sale

    # Table A2 on R1: company pays by bank transfer; the statement arrives later.
    o2 = open_bill(a, [("KOHV", 4)], table="A2")
    t = pay(a, o2["id"], "transfer", 1000, 1000, customer_name="OÜ Klient", customer_code="12345678", due_days=7)
    assert t.status_code == 201, t.text
    transfer_sale = t.json()["bill"]["sale_id"]
    ref = m.get("/api/v1/receivables/invoices").json()[0]["reference_no"]
    bank = m.post("/api/v1/receivables/bank-import", {"lines": [
        {"bank_tx_id": "EE-2026-0042", "booked_date": "2026-03-10", "amount_cents": 1000, "reference": ref,
         "payer": "OÜ Klient"}]})
    assert bank.status_code == 200 and bank.json()[0]["status"] == "matched", bank.text

    # One soup from the express sale comes back (manager approves; cash out of R1).
    refund = m.refund(express.json()["id"], [(1, 1)])
    assert refund.status_code == 201, refund.text

    run_worker(inv)
    run_worker(inv)                                                           # redelivery changes nothing
    return {"a": a, "m": m, "inv": inv, "express": express.json(), "table_sale": table_sale,
            "transfer_sale": transfer_sale, "refund": refund.json(), "order": o["id"], "d1": d1, "d2": d2,
            "bun": bun.json()["id"]}


def recon(api):
    r = api.get("/api/v1/admin/reconciliation")
    assert r.status_code == 200, r.text
    return r.json()


def by_check(rep):
    return {c["check"]: c for c in rep["checks"]}


def stock_qty(day, item):
    return next(x for x in day["m"].get("/api/v1/inventory/stock").json() if x["item_id"] == item)["qty_micro"]


def test_one_business_day_reconciles_across_every_register(day):
    m = day["m"]
    rep = recon(m)
    checks = by_check(rep)
    assert rep["ok"] is True, rep
    # All 13 invariants hold. The only open item is honest: the accounting export
    # rows wait for a delivery adapter that does not exist in main yet.
    assert {k: c["status"] for k, c in checks.items() if c["status"] != "ok"} == {"R8.books_export": "warning"}
    assert checks["R8.books_export"]["problem_count"] == 0
    assert checks["R8.books_export"]["pending"] == [
        {"export_rows_not_acknowledged": 4, "reason": "no accounting delivery adapter in main yet"}]
    assert rep["complete"] is False
    assert checks["R2.sale_totals"]["checked"] == 3 and checks["R6.events_per_document"]["checked"] == 4

    # Stock: 10 kg potato - 0.2 kg x (3 + 2) soups = 9 kg (a refund does not restock:
    # its disposition is #48); buns 20 - 4 = 16.
    assert stock_qty(day, day["inv"]["potato"]) == 9 * KG
    assert stock_qty(day, day["bun"]) == 16 * KG

    # Daily report = the three sales minus the refund.
    sales = [day["a"].get(f"/api/v1/sales/{sid}").json() for sid in
             (day["express"]["id"], day["table_sale"], day["transfer_sale"])]
    rpt = m.get("/api/v1/reports/daily?business_date=2026-03-10").json()
    assert rpt["sale_count"] == 3
    assert rpt["total_cents"] == sum(x["total_cents"] for x in sales)
    assert day["refund"]["total_cents"] == SUPP_GROSS
    assert rpt["net_total_cents"] == rpt["total_cents"] - SUPP_GROSS

    # Both drawers close without variance: R1 = 50.00 + express cash - refund; R2 = 20.00 + cash part.
    for reg, d in (("R1", day["d1"]), ("R2", day["d2"])):
        expected = day["a"].drawer(reg)["expected_cents"]
        r = day["a"].post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": expected})
        assert r.status_code == 200 and r.json()["variance_cents"] == 0, r.text
    assert recon(m)["ok"] is True


def test_trace_follows_a_table_sale_through_every_register(day):
    tr = day["m"].get(f"/api/v1/admin/trace/sales/{day['table_sale']}")
    assert tr.status_code == 200, tr.text
    tr = tr.json()
    assert tr["sale"]["register"] == "R2" and tr["sale"]["payment_method"] == "mixed"
    assert tr["order"]["id"] == day["order"] and tr["order"]["status"] == "paid"
    assert [t["status"] for t in tr["kitchen_tickets"]] == ["ready"]
    assert sorted((p["method"], p["status"]) for p in tr["payments"]) == [("card", "captured"), ("cash", "captured")]
    types = [e["type"] for e in tr["events"]]
    assert types.index("order.opened") < types.index("kitchen.ticket.created") < types.index("pos.sale.completed")
    sale_ev = next(e for e in tr["events"] if e["type"] == "pos.sale.completed")
    assert [x["consumer"] for x in sale_ev["applied_by"]] == ["books.export", "stock.consume"]
    assert sorted((mv["kind"], mv["item_id"]) for mv in tr["stock_movements"]) == sorted(
        [("sale_consumption", day["inv"]["potato"]), ("sale_consumption", day["bun"])])
    assert [(x["event_type"], x["status"]) for x in tr["accounting_export"]] == [("pos.sale.completed", "pending")]

    ex = day["m"].get(f"/api/v1/admin/trace/sales/{day['express']['id']}").json()
    assert [r["total_cents"] for r in ex["refunds"]] == [SUPP_GROSS]
    assert {e["type"] for e in ex["events"]} == {"pos.sale.completed", "pos.refund.completed"}


def test_reconciliation_and_trace_are_tenant_scoped(day, b):
    # Who may call it (owner / tenant-wide manager with MFA): tests/test_auth.py.
    assert b.as_role("manager").get(f"/api/v1/admin/trace/sales/{day['table_sale']}").status_code == 404
    other = recon(b.as_role("manager"))
    assert other["ok"] and all(c["checked"] == 0 for c in other["checks"] if c["check"] != "R7.event_pipeline")


# ------------------------------------------------- tampering is found and named


def tamper(app, sql, **params):
    """Change a register behind the application's back (owner connection:
    bypasses the ORM append-only guard and, on PostgreSQL, the app role)."""
    with app.state.admin_engine.begin() as c:
        c.execute(text(sql), params)


@pytest.mark.parametrize("sql, broken", [
    ("UPDATE drawer_sessions SET cash_sales_cents = cash_sales_cents + 100 WHERE id = :d1", "R1.drawer_cash"),
    ("UPDATE sale_lines SET gross_cents = gross_cents + 1 WHERE sale_id = :express AND line_no = 2",
     "R2.sale_totals"),
    ("UPDATE payments SET amount_cents = amount_cents - 1 WHERE method = 'card'", "R3.tenders"),
    ("UPDATE sale_refund_state SET refunded_gross_cents = 0 WHERE sale_id = :express", "R4.refunds"),
    ("UPDATE sales SET receipt_no = 99 WHERE id = :transfer", "R5.numbering"),
    ("UPDATE outbox_events SET payload_hash = 'x' WHERE aggregate_id = :express AND type = 'pos.sale.completed'",
     "R6.events_per_document"),
    ("DELETE FROM export_queue WHERE event_id IN (SELECT id FROM outbox_events WHERE aggregate_id = :table)",
     "R8.books_export"),
    ("DELETE FROM stock_movements WHERE ref_id = :table AND item_id = :bun", "R9.stock_consumption"),
    ("UPDATE stock_balances SET qty_micro = qty_micro + 1 WHERE item_id = :bun", "R10.stock_ledger"),
    ("UPDATE order_lines SET sent_quantity = 1 WHERE order_id = :order", "R11.orders_kitchen"),
    ("UPDATE customer_invoices SET status = 'issued'", "R12.receivables"),
])
def test_each_tampered_register_is_named(day, sql, broken):
    assert recon(day["m"])["ok"]
    tamper(day["inv"]["app"], sql, d1=day["d1"]["id"], express=day["express"]["id"], table=day["table_sale"],
           transfer=day["transfer_sale"], order=day["order"], bun=day["bun"])
    rep = recon(day["m"])
    failed = [c["check"] for c in rep["checks"] if c["status"] == "fail"]
    assert rep["ok"] is False and broken in failed, rep
    assert by_check(rep)[broken]["problem_count"] >= 1 and by_check(rep)[broken]["problems"]


def test_a_dead_letter_and_a_lagging_consumer_are_reported(day):
    app = day["inv"]["app"]
    a = day["a"]
    a.sell([("KOHV", 1)], 250)                                                 # not consumed yet
    rep = by_check(recon(day["m"]))
    assert rep["R7.event_pipeline"]["status"] == "warning"
    assert {p["consumer"] for p in rep["R7.event_pipeline"]["pending"]} >= {"books.export", "stock.consume"}
    assert rep["R9.stock_consumption"]["pending"] == [{"sale_id": rep["R9.stock_consumption"]["pending"][0]["sale_id"],
                                                       "reason": "stock not consumed yet"}]
    assert rep["R9.stock_consumption"]["problem_count"] == 0
    tamper(app, "INSERT INTO event_dead_letters (consumer, tenant_id, event_id, recorded_seq, attempts, error, "
                "created_at) VALUES ('stock.consume', 't-a', 'x', 1, 5, 'boom', CURRENT_TIMESTAMP)")
    rep = recon(day["m"])
    assert not rep["ok"] and by_check(rep)["R7.event_pipeline"]["status"] == "fail"


def test_offline_till_entries_reconcile_with_the_central_documents(edge, central, app, clock):
    """R13 on the central server: each accepted till entry created exactly one
    sale or refund; breaking that link is reported."""
    from test_offline_sync import Net, agent

    edge.open_drawer(10_000)
    sale = edge.sell([("KOHV", 2)], 1000).json()
    assert edge.as_role("manager").refund(sale["id"], [(1, 1)]).status_code == 201
    agent(edge, Net(central.c), clock).run_until_idle()
    m = central.as_role("manager")
    rep = recon(m)
    assert rep["ok"] and by_check(rep)["R13.offline_sync"] == {**by_check(rep)["R13.offline_sync"],
                                                               "status": "ok", "checked": 2}
    tamper(app, "UPDATE device_inbox SET sale_id = 'missing' WHERE entry_type = 'refund'")
    rep = recon(m)
    assert not rep["ok"] and by_check(rep)["R13.offline_sync"]["problems"][0]["reason"] == \
        "accepted entry without its document"
