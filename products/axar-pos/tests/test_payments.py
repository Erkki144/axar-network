"""C3 bill payments (#15): open bills, mixed tenders, MOCK card terminal,
bank-transfer receivables and bank matching. Runs on SQLite and PostgreSQL."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from sqlalchemy import select

from axar_pos import payments
from axar_pos.models import BankTransaction, CustomerInvoice, OutboxEvent, Payment, Sale


def table_id(api, code="A1"):
    return next(t["id"] for t in api.get("/api/v1/tables").json() if t["code"] == code)


def open_bill(api, lines, table="A1"):
    o = api.post(f"/api/v1/tables/{table_id(api, table)}/order").json()
    r = api.c.put(f"/api/v1/orders/{o['id']}/lines", headers=api.h, json={"version": o["version"], "lines": [
        {"product_id": api.product_id(sku), "quantity": q} for sku, q in lines]})
    assert r.status_code == 200, r.text
    return r.json()


def bill(api, oid):
    r = api.get(f"/api/v1/orders/{oid}/bill")
    assert r.status_code == 200, r.text
    return r.json()


def pay(api, oid, method, amount, total, key=None, register="R1", **kw):
    body = {"method": method, "register_id": api.register_id(register), "expected_total_cents": total,
            "amount_cents": amount, **kw}
    return api.post(f"/api/v1/orders/{oid}/payments", body, headers={"Idempotency-Key": key or str(uuid.uuid4())})


def outbox(app, type_):
    with app.state.session_factory() as s:
        return [e for e in s.scalars(select(OutboxEvent).where(OutboxEvent.type == type_)
                                     .order_by(OutboxEvent.recorded_seq))]


# ------------------------------------------------------------- the €85 split


def test_85_euro_bill_split_cash_card_transfer_then_bank(a, app):
    """AION #15: €85 = €20 cash + €45 card + €20 bank transfer."""
    d = a.open_drawer(5000)
    o = open_bill(a, [("KOHV", 34)])                     # 34 x 2.50 = 85.00
    b0 = bill(a, o["id"])
    assert (b0["total_cents"], b0["remaining_cents"], b0["status"]) == (8500, 8500, "open")

    r = pay(a, o["id"], "cash", 2000, 8500, tendered_cents=5000)
    assert r.status_code == 201, r.text
    p, bl = r.json()["payment"], r.json()["bill"]
    assert (p["status"], p["change_cents"], p["rounding_cents"]) == ("captured", 3000, 0)
    assert (bl["status"], bl["paid_cents"], bl["remaining_cents"], bl["sale_id"]) == ("partially_paid", 2000, 6500, None)

    r = pay(a, o["id"], "card", 4500, 8500, simulate="approved")
    assert r.status_code == 201, r.text
    assert r.json()["payment"]["status"] == "captured"
    assert r.json()["bill"]["remaining_cents"] == 2000 and r.json()["bill"]["sale_id"] is None

    r = pay(a, o["id"], "transfer", 2000, 8500, customer_name="OÜ Näide", customer_code="12345678", due_days=7)
    assert r.status_code == 201, r.text
    bl = r.json()["bill"]
    # Bill covered -> ONE sale issued; transfer part is a receivable, not "paid".
    assert (bl["status"], bl["remaining_cents"], bl["paid_cents"], bl["pending_transfer_cents"]) == (
        "invoiced", 0, 6500, 2000)
    assert bl["sale_id"]
    sale = a.get(f"/api/v1/sales/{bl['sale_id']}").json()
    assert (sale["payment_method"], sale["total_cents"], sale["cash_due_cents"]) == ("mixed", 8500, 2000)

    ev = outbox(app, "pos.sale.completed")
    assert len(ev) == 1 and ev[0].version == 2
    pl = json.loads(ev[0].payload)
    assert sorted((t["method"], t["status"], t["amount_cents"]) for t in pl["tenders"]) == [
        ("card", "captured", 4500), ("cash", "captured", 2000), ("transfer", "pending", 2000)]
    closed = outbox(app, "order.closed")
    assert [json.loads(e.payload)["status"] for e in closed] == ["invoiced"] and closed[0].version == 2

    # Receivable with a valid Estonian reference number.
    m = a.as_role("manager")
    inv = m.get("/api/v1/receivables/invoices").json()
    assert len(inv) == 1 and inv[0]["outstanding_cents"] == 2000 and inv[0]["status"] == "issued"
    assert payments.valid_reference(inv[0]["reference_no"])

    # Drawer: only the €20 cash (no card, no transfer) is expected in the till.
    assert a.drawer()["expected_cents"] == 5000 + 2000
    r = a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 7000})
    assert r.status_code == 200, r.text
    assert r.json()["variance_cents"] == 0

    # Bank statement arrives: matched by reference -> invoice paid -> bill paid.
    r = m.post("/api/v1/receivables/bank-import", {"lines": [
        {"bank_tx_id": "EE-2026-0001", "booked_date": "2026-03-12", "amount_cents": 2000,
         "reference": inv[0]["reference_no"], "payer": "OÜ Näide"}]})
    assert r.status_code == 200, r.text
    assert r.json()[0]["status"] == "matched" and r.json()[0]["allocated_cents"] == 2000
    bl = bill(a, o["id"])
    assert (bl["status"], bl["paid_cents"], bl["pending_transfer_cents"]) == ("paid", 8500, 0)
    assert m.get("/api/v1/receivables/invoices").json()[0]["status"] == "paid"
    changed = [json.loads(e.payload) for e in outbox(app, "order.changed") if e.version == 2]
    assert changed[-1]["status"] == "paid" and changed[-1]["sale_id"] == bl["sale_id"]
    assert len(outbox(app, "pos.sale.completed")) == 1   # still ONE sale


def test_bill_invariant_holds_after_every_step(a):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 3), ("SAI", 2)])           # 750 + 398 = 1148
    steps = [("card", 300, {"simulate": "declined"}), ("card", 300, {"simulate": "timeout"}),
             ("cash", 200, {"tendered_cents": 200}), ("transfer", 100, {"customer_name": "Mari Maasikas"})]
    for method, amount, kw in steps:
        assert pay(a, o["id"], method, amount, 1148, **kw).status_code == 201
        bl = bill(a, o["id"])
        assert bl["paid_cents"] + bl["pending_transfer_cents"] + bl["in_flight_cents"] + bl["remaining_cents"] == 1148
        assert bl["remaining_cents"] >= 0
    assert bill(a, o["id"])["remaining_cents"] == 1148 - 300 - 200 - 100


# ------------------------------------------------------------ cash rounding


def test_cash_rounding_only_on_final_cash_part(a):
    a.open_drawer(0)
    o = open_bill(a, [("KOHV", 1), ("SAI", 1)])           # 449
    r = pay(a, o["id"], "cash", 101, 449, tendered_cents=200)
    assert r.status_code == 422 and r.json()["error"] == "invalid_payment"   # partial cash: 5 c steps
    assert pay(a, o["id"], "card", 100, 449).status_code == 201
    r = pay(a, o["id"], "cash", 349, 449, tendered_cents=500)
    assert r.status_code == 201, r.text
    p = r.json()["payment"]
    assert (p["rounding_cents"], p["change_cents"]) == (1, 150)            # 3.49 -> 3.50
    sale = a.get(f"/api/v1/sales/{r.json()['bill']['sale_id']}").json()
    assert (sale["total_cents"], sale["cash_rounding_cents"], sale["cash_due_cents"]) == (449, 1, 350)
    assert a.drawer()["expected_cents"] == 350


def test_insufficient_tender_and_change_only_from_cash(a):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    r = pay(a, o["id"], "cash", 500, 500, tendered_cents=400)
    assert r.status_code == 422 and r.json()["error"] == "insufficient_tender"
    r = pay(a, o["id"], "card", 600, 500)
    assert r.status_code == 422 and r.json()["error"] == "overpayment"     # no change from card


# ---------------------------------------------------------- guards & idempotency


def test_double_tap_same_key_records_one_payment(a, app):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 4)])
    r1 = pay(a, o["id"], "cash", 500, 1000, key="pay-key-000001", tendered_cents=500)
    r2 = pay(a, o["id"], "cash", 500, 1000, key="pay-key-000001", tendered_cents=500)
    assert (r1.status_code, r2.status_code) == (201, 200)
    assert r2.headers["Idempotent-Replayed"] == "true"
    assert r1.json()["payment"]["id"] == r2.json()["payment"]["id"]
    r3 = pay(a, o["id"], "cash", 1000, 1000, key="pay-key-000001", tendered_cents=1000)
    assert r3.status_code == 409 and r3.json()["error"] == "idempotency_key_reused"
    assert bill(a, o["id"])["paid_cents"] == 500
    assert a.drawer()["expected_cents"] == 5000 + 500


def test_changed_bill_and_closed_bill_are_rejected(a):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    r = pay(a, o["id"], "card", 100, 250)                 # device showed an older total
    assert r.status_code == 409 and r.json()["error"] == "bill_changed"
    assert pay(a, o["id"], "card", 500, 500).status_code == 201
    r = pay(a, o["id"], "card", 1, 500)
    assert r.status_code == 409 and r.json()["error"] == "bill_closed"


def test_partially_paid_bill_is_frozen_and_still_the_tables_bill(a):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    assert pay(a, o["id"], "card", 100, 500).status_code == 201
    # Same table -> same bill (one open-or-partially-paid bill per table).
    again = a.post(f"/api/v1/tables/{table_id(a)}/order").json()
    assert again["id"] == o["id"] and again["status"] == "partially_paid"
    # Lines cannot change after money was taken; legacy one-shot pay is refused.
    r = a.c.put(f"/api/v1/orders/{o['id']}/lines", headers=a.h, json={"version": again["version"], "lines": [
        {"product_id": a.product_id("KOHV"), "quantity": 1}]})
    assert r.status_code == 409
    r = a.post(f"/api/v1/orders/{o['id']}/pay-cash", {"version": again["version"], "register_id": a.register_id(),
               "tendered_cents": 1000}, headers={"Idempotency-Key": "legacy-pay-0001"})
    assert r.status_code == 409
    assert bill(a, o["id"])["paid_cents"] == 100


def test_cash_needs_open_drawer_card_does_not_until_settlement(a):
    o = open_bill(a, [("KOHV", 2)])
    r = pay(a, o["id"], "cash", 100, 500, tendered_cents=100)
    assert r.status_code == 409 and r.json()["error"] == "no_open_drawer"
    assert pay(a, o["id"], "card", 100, 500).status_code == 201
    # The receipt (sale) is issued on a register with an open drawer.
    r = pay(a, o["id"], "card", 400, 500)
    assert r.status_code == 409 and r.json()["error"] == "no_open_drawer"
    assert bill(a, o["id"])["paid_cents"] == 100                  # whole step rolled back
    a.open_drawer()
    assert pay(a, o["id"], "card", 400, 500).status_code == 201
    assert bill(a, o["id"])["status"] == "paid"


# ------------------------------------------------------------- card (MOCK)


def test_card_declined_leaves_bill_open(a):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    r = pay(a, o["id"], "card", 500, 500, simulate="declined")
    assert r.status_code == 201 and r.json()["payment"]["status"] == "declined"
    bl = r.json()["bill"]
    assert (bl["status"], bl["remaining_cents"], bl["sale_id"]) == ("open", 500, None)
    assert pay(a, o["id"], "card", 500, 500).status_code == 201       # retry with a NEW key
    assert bill(a, o["id"])["status"] == "paid"


def test_card_unknown_blocks_settlement_until_reconciled(a, app):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    r = pay(a, o["id"], "card", 300, 500, simulate="timeout")
    p = r.json()["payment"]
    assert p["status"] == "unknown"
    bl = r.json()["bill"]
    assert (bl["in_flight_cents"], bl["remaining_cents"], bl["status"]) == (300, 200, "partially_paid")
    r = pay(a, o["id"], "cash", 500, 500, tendered_cents=500)         # cannot pay the reserved part twice
    assert r.status_code == 422 and r.json()["error"] == "overpayment"
    assert pay(a, o["id"], "cash", 200, 500, tendered_cents=200).status_code == 201
    assert bill(a, o["id"])["sale_id"] is None                         # not settled while unknown
    r = a.post(f"/api/v1/payments/{p['id']}/reconcile")
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["paid_cents"]) == ("paid", 500)
    assert len(outbox(app, "pos.sale.completed")) == 1
    r = a.post(f"/api/v1/payments/{p['id']}/reconcile")                # idempotent
    assert r.status_code == 200 and r.json()["status"] == "paid"


# ------------------------------------------------------------ bank matching


def _transfer_bill(a, amount_cents=1000, customer="OÜ Klient"):
    a.open_drawer()
    o = open_bill(a, [("KOHV", amount_cents // 250)])
    r = pay(a, o["id"], "transfer", amount_cents, amount_cents, customer_name=customer)
    assert r.status_code == 201, r.text
    m = a.as_role("manager")
    return o, m, m.get("/api/v1/receivables/invoices").json()[0]


def test_partial_then_rest_overpay_and_duplicate_bank_lines(a, app):
    o, m, inv = _transfer_bill(a, 1000)
    ref = inv["reference_no"]
    line = lambda tx, cents, r=ref: {"bank_tx_id": tx, "booked_date": "2026-03-11", "amount_cents": cents,
                                     "reference": r, "payer": "OÜ Klient"}
    out = m.post("/api/v1/receivables/bank-import", {"lines": [line("TX1", 400), line("TX1", 400)]}).json()
    assert [x["status"] for x in out] == ["partial", "duplicate"]
    assert m.get("/api/v1/receivables/invoices").json()[0]["paid_cents"] == 400
    assert bill(a, o["id"])["status"] == "invoiced"
    out = m.post("/api/v1/receivables/bank-import", {"lines": [line("TX2", 700)]}).json()
    assert out[0]["status"] == "overpaid" and out[0]["allocated_cents"] == 600     # surplus kept for a human
    assert bill(a, o["id"])["status"] == "paid"
    out = m.post("/api/v1/receivables/bank-import", {"lines": [line("TX3", 50), line("TX4", 50, "12345678901")]}).json()
    assert [x["status"] for x in out] == ["overpaid", "unmatched"]
    with app.state.session_factory() as s:
        assert s.scalar(select(CustomerInvoice.paid_cents)) == 1000
        assert len(list(s.scalars(select(BankTransaction.id)))) == 4


def test_bank_lines_never_match_another_tenants_invoice(a, b):
    _o, _m, inv = _transfer_bill(a, 500)
    mb = b.as_role("manager")
    out = mb.post("/api/v1/receivables/bank-import", {"lines": [
        {"bank_tx_id": "B-1", "booked_date": "2026-03-11", "amount_cents": 500,
         "reference": inv["reference_no"], "payer": "x"}]}).json()
    assert out[0]["status"] == "unmatched"
    assert mb.get("/api/v1/receivables/invoices").json() == []
    assert a.as_role("manager").get("/api/v1/receivables/invoices").json()[0]["status"] == "issued"


def test_transfer_requires_customer(a):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    r = pay(a, o["id"], "transfer", 500, 500, customer_name=" ")
    assert r.status_code == 422 and r.json()["error"] == "invalid_payment"


def test_reference_numbers_7_3_1():
    assert payments.reference_number(1234) == "12344"
    for base in (1, 1000, 1001, 987654321):
        ref = payments.reference_number(base)
        assert payments.valid_reference(ref)
        bad = ref[:-1] + str((int(ref[-1]) + 1) % 10)
        assert not payments.valid_reference(bad)
    assert not payments.valid_reference("abc") and not payments.valid_reference("")


# ------------------------------------------------------ downstream & refunds


def test_v2_sale_feeds_books_export_and_reports(a, app, settings):
    from axar_pos import worker
    from axar_pos.models import ExportQueue

    a.open_drawer()
    o = open_bill(a, [("KOHV", 4)])
    assert pay(a, o["id"], "card", 600, 1000).status_code == 201
    assert pay(a, o["id"], "cash", 400, 1000, tendered_cents=500).status_code == 201
    assert a.sell([("KOHV", 1)], 250).status_code == 201                 # plus a plain cash sale
    worker.run_once(app.state.request_factory, settings, "w")
    with app.state.session_factory() as s:
        q = list(s.scalars(select(ExportQueue).order_by(ExportQueue.recorded_seq)))
    assert [(x.event_type, x.event_version) for x in q] == [("pos.sale.completed", 2), ("pos.sale.completed", 1)]
    rep = a.as_role("manager").get("/api/v1/reports/daily", params={"business_date": "2026-03-10"}).json()
    assert rep["total_cents"] == 1250 and rep["cash_collected_cents"] == 400 + 250
    assert a.drawer()["expected_cents"] == 5000 + 400 + 250


def test_mixed_sale_cannot_be_refunded_in_cash(a):
    a.open_drawer(50_000)
    o = open_bill(a, [("KOHV", 2)])
    pay(a, o["id"], "card", 200, 500)
    sale_id = pay(a, o["id"], "cash", 300, 500, tendered_cents=300).json()["bill"]["sale_id"]
    r = a.as_role("manager").refund(sale_id, [(1, 1)])
    assert r.status_code == 422 and r.json()["error"] == "invalid_refund"


def test_all_cash_bill_refund_and_drawer_close_balance(a):
    d = a.open_drawer(5000)
    o = open_bill(a, [("KOHV", 2)])
    pay(a, o["id"], "cash", 200, 500, tendered_cents=200)
    sale_id = pay(a, o["id"], "cash", 300, 500, tendered_cents=300).json()["bill"]["sale_id"]
    r = a.as_role("manager").refund(sale_id, [(1, 1)])
    assert r.status_code == 201, r.text
    r = a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5000 + 500 - 250})
    assert r.status_code == 200, r.text                                   # ledger == running totals
    assert r.json()["variance_cents"] == 0


def test_tenant_isolation_of_bills_and_payments(a, b):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    r = b.post(f"/api/v1/orders/{o['id']}/payments", {"method": "card", "register_id": b.register_id(),
               "expected_total_cents": 500, "amount_cents": 500}, headers={"Idempotency-Key": "b-steal-0001"})
    assert r.status_code == 404
    assert b.get(f"/api/v1/orders/{o['id']}/bill").status_code == 404
    p = pay(a, o["id"], "card", 100, 500, simulate="timeout").json()["payment"]
    assert b.post(f"/api/v1/payments/{p['id']}/reconcile").status_code == 404


# ------------------------------------------------------------ concurrency


def test_parallel_devices_never_overpay_a_bill(a, app):
    """8 devices pay €20 each on an €85 bill at once: exactly 4 succeed."""
    a.open_drawer(register="R1"); a.open_drawer(register="R2")
    o = open_bill(a, [("KOHV", 34)])
    with ThreadPoolExecutor(8) as pool:
        rs = list(pool.map(lambda i: pay(a, o["id"], "cash" if i % 2 else "card", 2000, 8500,
                                         register="R1" if i % 2 else "R2", tendered_cents=2000), range(8)))
    ok = [r for r in rs if r.status_code == 201]
    assert len(ok) == 4, [r.text for r in rs]
    assert all(r.status_code == 422 and r.json()["error"] == "overpayment" for r in rs if r.status_code != 201)
    bl = bill(a, o["id"])
    assert (bl["paid_cents"], bl["remaining_cents"], bl["status"]) == (8000, 500, "partially_paid")
    with app.state.session_factory() as s:
        assert sum(s.scalars(select(Payment.amount_cents).where(Payment.status == "captured"))) == 8000


def test_parallel_same_key_records_one_payment(a, app):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 34)])
    with ThreadPoolExecutor(8) as pool:
        rs = list(pool.map(lambda _: pay(a, o["id"], "cash", 2000, 8500, key="same-pay-key-1",
                                         tendered_cents=2000), range(8)))
    assert {r.status_code for r in rs} <= {200, 201}, [r.text for r in rs]
    assert len({r.json()["payment"]["id"] for r in rs}) == 1
    assert a.drawer()["expected_cents"] == 5000 + 2000


def test_parallel_final_payments_issue_exactly_one_sale(a, app):
    a.open_drawer(register="R1"); a.open_drawer(register="R2")
    o = open_bill(a, [("KOHV", 4)])
    assert pay(a, o["id"], "card", 500, 1000).status_code == 201
    with ThreadPoolExecutor(6) as pool:
        rs = list(pool.map(lambda i: pay(a, o["id"], "card", 500, 1000, register="R1" if i % 2 else "R2"),
                           range(6)))
    assert sum(r.status_code == 201 for r in rs) == 1
    with app.state.session_factory() as s:
        assert len(list(s.scalars(select(Sale.id)))) == 1
    assert len(outbox(app, "pos.sale.completed")) == 1


def test_parallel_import_of_one_bank_line_applies_it_once(a, app):
    _o, m, inv = _transfer_bill(a, 1000)
    body = {"lines": [{"bank_tx_id": "PAR-1", "booked_date": "2026-03-11", "amount_cents": 1000,
                       "reference": inv["reference_no"], "payer": "OÜ Klient"}]}
    with ThreadPoolExecutor(6) as pool:
        rs = list(pool.map(lambda _: m.post("/api/v1/receivables/bank-import", body), range(6)))
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs]
    assert sorted(r.json()[0]["status"] for r in rs) == ["duplicate"] * 5 + ["matched"]
    with app.state.session_factory() as s:
        assert s.scalar(select(CustomerInvoice.paid_cents)) == 1000


def test_bill_sale_gets_register_number_and_journal_entry(a, app):
    """C3 + O1a: the one sale a settled bill produces is numbered and
    journaled exactly like a direct cash sale."""
    from axar_pos import journal
    from axar_pos.models import DeviceJournalEntry

    a.open_drawer()
    assert a.sell([("KOHV", 1)], 500).status_code == 201                  # register number 1
    o = open_bill(a, [("KOHV", 2)])
    pay(a, o["id"], "card", 200, 500)
    sale_id = pay(a, o["id"], "cash", 300, 500, tendered_cents=300).json()["bill"]["sale_id"]
    sale = a.get(f"/api/v1/sales/{sale_id}").json()
    assert sale["register_receipt_no"] == 2
    with app.state.session_factory() as s:
        es = list(s.scalars(select(DeviceJournalEntry).where(
            DeviceJournalEntry.stream_id == journal.stream_for_register(a.register_id()))
            .order_by(DeviceJournalEntry.seq)))
    assert [(e.seq, e.entity_id) for e in es][-1] == (2, sale_id)
    v = a.as_role("manager").get(f"/api/v1/admin/registers/{a.register_id()}/journal/verify").json()
    assert v["ok"] is True and v["entries"] == 2
