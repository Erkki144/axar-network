"""Issue #3 (part 1): simulated cash refunds as separate auditable records."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, update

from axar_pos import services
from axar_pos.models import (
    AppendOnlyViolation,
    DrawerSession,
    OutboxEvent,
    Refund,
    RefundAllocation,
    RefundLine,
    Sale,
    SaleRefundState,
)


@pytest.fixture
def m(a):
    """Manager on tenant A."""
    return a.as_role("manager")


def n_refunds(app):
    with app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(Refund))


def sold(a, lines=(("KOHV", 2), ("SAI", 1)), tendered=1000, register="R1"):
    r = a.sell(list(lines), tendered, register=register)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ happy paths


def test_full_refund_mirrors_sale_and_leaves_original_untouched(a, m, app):
    a.open_drawer(5000)
    sale = sold(a)  # 6.99 -> cash 7.00
    before = a.get(f"/api/v1/sales/{sale['id']}").json()

    r = m.refund(sale["id"], [(1, 2), (2, 1)])
    assert r.status_code == 201, r.text
    rf = r.json()
    assert rf["refund_no"] == 1 and rf["original_receipt_no"] == 1
    assert (rf["total_cents"], rf["vat_cents"], rf["net_cents"]) == (699, 136, 563)
    assert (rf["cash_paid_cents"], rf["cash_rounding_cents"]) == (700, 1)
    assert rf["approver_role"] == "manager" and rf["simulated"] is True
    assert [ln["vat_cents"] for ln in rf["lines"]] == [97, 39]

    after = a.get(f"/api/v1/sales/{sale['id']}").json()
    for k in ("total_cents", "vat_cents", "cash_due_cents", "receipt_no", "correlation_id"):
        assert after[k] == before[k]
    assert [ln["refunded_quantity"] for ln in after["lines"]] == [2, 1]

    d = a.drawer()
    assert (d["refund_count"], d["cash_refunds_cents"]) == (1, 700)
    assert d["expected_cents"] == 5000 + 700 - 700


def test_partial_refunds_sum_exactly_to_original_vat(a, m):
    a.open_drawer()
    sale = sold(a, [("KOHV", 3)], 750)  # gross 750, VAT 145 (145.16)
    assert sale["vat_cents"] == 145
    vats = []
    for _ in range(3):
        r = m.refund(sale["id"], [(1, 1)])
        assert r.status_code == 201, r.text
        vats.append(r.json()["vat_cents"])
    # Cumulative pro-rata of 145: half_up(145*1/3)=48, half_up(145*2/3)=97,
    # 145 -> refunds 48, 49, 48 (no remainder dumped on the last refund).
    assert vats == [48, 49, 48]
    assert sum(vats) == sale["vat_cents"]


def test_refund_rounding_for_odd_amounts(a, m):
    a.open_drawer()
    sale = sold(a, [("SAI", 2)], 400)  # 3.98 -> cash 4.00
    r1 = m.refund(sale["id"], [(1, 1)]).json()  # 1.99 -> 2.00
    r2 = m.refund(sale["id"], [(1, 1)]).json()
    assert (r1["cash_paid_cents"], r2["cash_paid_cents"]) == (200, 200)
    assert r1["total_cents"] + r2["total_cents"] == sale["total_cents"]


def test_refund_receipt_text(a, m):
    a.open_drawer()
    sale = sold(a)
    rf = m.refund(sale["id"], [(1, 1)], reason="kohv oli külm").json()
    t = a.get(f"/api/v1/refunds/{rf['id']}/receipt.txt").text
    assert "TAGASTUS" in t and "SIMULEERITUD" in t
    assert "Tagastus nr 1" in t and "Algne tšekk nr 1" in t
    assert "Põhjus: kohv oli külm" in t and "(manager)" in t
    assert "-1 x 2,50 €" in t and "Sularahas välja" in t and "2,50 €" in t


# ------------------------------------------------------------- validation/guards


def test_over_refund_rejected_and_nothing_written(a, m, app):
    a.open_drawer()
    sale = sold(a)
    r = m.refund(sale["id"], [(1, 3)])
    assert r.status_code == 409 and r.json()["error"] == "over_refund"
    assert m.refund(sale["id"], [(1, 2)]).status_code == 201
    r = m.refund(sale["id"], [(1, 1)])
    assert r.status_code == 409 and r.json()["error"] == "over_refund"
    assert n_refunds(app) == 1


def test_failed_second_line_rolls_back_first_line_counter(a, m, app):
    a.open_drawer()
    sale = sold(a)
    r = m.refund(sale["id"], [(1, 1), (2, 5)])  # line 2 over-refunds
    assert r.status_code == 409
    with app.state.session_factory() as s:
        assert sum(x.refunded_quantity for x in s.scalars(select(RefundAllocation))) == 0
    assert a.drawer()["refund_count"] == 0


@pytest.mark.parametrize("lines,status", [
    ([(9, 1)], 422),          # no such line
    ([(1, 1), (1, 1)], 422),  # duplicate line
])
def test_invalid_refund_lines(a, m, app, lines, status):
    a.open_drawer()
    sale = sold(a)
    r = m.refund(sale["id"], lines)
    assert r.status_code == status
    assert n_refunds(app) == 0


@pytest.mark.parametrize("body", [
    {"lines": [{"line_no": 1, "quantity": 0}], "reason": "xxx"},
    {"lines": [{"line_no": 1, "quantity": 1.0}], "reason": "xxx"},
    {"lines": [], "reason": "xxx"},
    {"lines": [{"line_no": 1, "quantity": 1}], "reason": ""},
    {"lines": [{"line_no": 1, "quantity": 1}], "reason": "xxx", "total_cents": 1},
    {"lines": [{"line_no": 1, "quantity": 1, "gross_cents": 1}], "reason": "xxx"},
])
def test_refund_schema_rejects_bad_input(a, m, body):
    a.open_drawer()
    sale = sold(a)
    body = {**body, "register_id": a.register_id()}
    r = m.post(f"/api/v1/sales/{sale['id']}/refunds", body,
               headers={"Idempotency-Key": "schema-refund-1"})
    assert r.status_code == 422


def test_cashier_cannot_refund_and_invalid_role_rejected(a, app):
    a.open_drawer()
    sale = sold(a)
    r = a.refund(sale["id"], [(1, 1)])  # default role: cashier
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    body = {"register_id": a.register_id(), "lines": [{"line_no": 1, "quantity": 1}],
            "reason": "proov"}
    r = a.as_role("superuser").post(f"/api/v1/sales/{sale['id']}/refunds", body,
                                    headers={"Idempotency-Key": "bad-role-0001"})
    assert r.status_code == 403
    assert a.as_role("superuser").get("/api/v1/products").status_code == 403
    assert a.as_role("owner").refund(sale["id"], [(1, 1)]).status_code == 201
    assert n_refunds(app) == 1


# ------------------------------------------------------------------ idempotency


def test_refund_retry_same_key_replays(a, m, app):
    a.open_drawer()
    sale = sold(a)
    r1 = m.refund(sale["id"], [(1, 1)], key="refund-key-0001")
    r2 = m.refund(sale["id"], [(1, 1)], key="refund-key-0001")
    assert (r1.status_code, r2.status_code) == (201, 200)
    assert r2.headers["Idempotent-Replayed"] == "true"
    assert r1.json() == r2.json()
    assert n_refunds(app) == 1
    assert a.drawer()["refund_count"] == 1


def test_refund_same_key_different_body_conflicts(a, m, app):
    a.open_drawer()
    sale = sold(a)
    m.refund(sale["id"], [(1, 1)], key="refund-key-0002")
    r = m.refund(sale["id"], [(1, 2)], key="refund-key-0002")
    assert r.status_code == 409 and r.json()["error"] == "idempotency_key_reused"
    assert n_refunds(app) == 1


def test_refund_key_namespace_separate_from_sales(a, m):
    a.open_drawer()
    sale = sold(a)
    r = m.refund(sale["id"], [(1, 1)], key=f"shared-{sale['id'][:8]}")
    assert r.status_code == 201


# ------------------------------------------------------------------ concurrency


def test_parallel_full_refunds_only_one_wins(a, m, app):
    a.open_drawer()
    sale = sold(a)
    body = {"register_id": a.register_id(),
            "lines": [{"line_no": 1, "quantity": 2}, {"line_no": 2, "quantity": 1}],
            "reason": "topelt vajutus"}

    def fire(i):
        return m.post(f"/api/v1/sales/{sale['id']}/refunds", body,
                      headers={"Idempotency-Key": f"parallel-refund-{i:04d}"})

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fire, range(10)))

    assert sum(r.status_code == 201 for r in results) == 1
    assert all(r.json()["error"] == "over_refund" for r in results if r.status_code != 201)
    assert n_refunds(app) == 1
    d = a.drawer()
    assert (d["refund_count"], d["cash_refunds_cents"]) == (1, 700)


def test_stale_allocation_read_cannot_over_refund(a, m, app, monkeypatch):
    """Deterministic interleaving: refund A reads 'refundable 2', refund B
    commits for 2, then A tries to claim. The compare-and-swap must reject A."""
    a.open_drawer()
    sale = sold(a)
    real = services.refund_state
    fired = []

    def stale_state(s, tenant_id, sale_id):
        snapshot = {k: RefundAllocation(sale_line_id=v.sale_line_id, tenant_id=v.tenant_id,
                                        sale_id=v.sale_id,
                                        original_quantity=v.original_quantity,
                                        refunded_quantity=v.refunded_quantity,
                                        refunded_gross_cents=v.refunded_gross_cents,
                                        refunded_vat_cents=v.refunded_vat_cents)
                    for k, v in real(s, tenant_id, sale_id).items()}
        if not fired:
            fired.append(1)
            # Concurrent refund commits right after our read (same-session
            # commit: SQLite would otherwise block the second writer).
            line1 = next(iter(snapshot))
            s.execute(update(RefundAllocation)
                      .where(RefundAllocation.sale_line_id == line1)
                      .values(refunded_quantity=2, refunded_gross_cents=500,
                              refunded_vat_cents=97))
            s.commit()
        return snapshot

    monkeypatch.setattr(services, "refund_state", stale_state)
    r = m.refund(sale["id"], [(1, 2)])
    monkeypatch.setattr(services, "refund_state", real)

    assert fired
    assert r.status_code == 409 and r.json()["error"] == "over_refund"
    with app.state.session_factory() as s:
        line1 = s.scalars(select(RefundAllocation).order_by(RefundAllocation.sale_line_id)).first()
        assert line1.refunded_quantity == 2  # not 4
    assert n_refunds(app) == 0


def test_stale_read_cannot_cause_vat_drift_on_last_unit(a, m, app, monkeypatch):
    """3 x 2.50: VAT 145; cumulative targets 48 / 97 / 145. If a concurrent
    refund makes *our* unit the last one after we read, the compare-and-swap
    rejects us so the retry computes 145 − 97 = 48. Without CAS we would
    compute from the stale count (97 − 48 = 49) and over-refund VAT by 1."""
    a.open_drawer()
    sale = sold(a, [("KOHV", 3)], 750)
    assert m.refund(sale["id"], [(1, 1)]).json()["vat_cents"] == 48
    real = services.refund_state
    fired = []

    def stale_state(s, tenant_id, sale_id):
        snap = {k: RefundAllocation(sale_line_id=v.sale_line_id, tenant_id=v.tenant_id,
                                    sale_id=v.sale_id, original_quantity=v.original_quantity,
                                    refunded_quantity=v.refunded_quantity,
                                    refunded_gross_cents=v.refunded_gross_cents,
                                    refunded_vat_cents=v.refunded_vat_cents)
                for k, v in real(s, tenant_id, sale_id).items()}
        if not fired:
            fired.append(1)  # concurrent refund of the 2nd unit commits now
            s.execute(update(RefundAllocation).values(
                refunded_quantity=RefundAllocation.refunded_quantity + 1,
                refunded_gross_cents=RefundAllocation.refunded_gross_cents + 250,
                refunded_vat_cents=RefundAllocation.refunded_vat_cents + 49))
            s.execute(update(SaleRefundState).values(
                refunded_gross_cents=SaleRefundState.refunded_gross_cents + 250,
                refunded_cash_cents=SaleRefundState.refunded_cash_cents + 250))
            s.commit()
        return snap

    monkeypatch.setattr(services, "refund_state", stale_state)
    r = m.refund(sale["id"], [(1, 1)])
    monkeypatch.setattr(services, "refund_state", real)
    assert r.status_code == 409 and r.json()["error"] == "over_refund"

    retry = m.refund(sale["id"], [(1, 1)])
    assert retry.status_code == 201
    assert retry.json()["vat_cents"] == 48
    with app.state.session_factory() as s:
        alloc = s.scalar(select(RefundAllocation))
    assert (alloc.refunded_quantity, alloc.refunded_vat_cents) == (3, 145)


# --------------------------------------------------------------- drawer / cash


def test_refund_needs_open_drawer_on_that_register(a, m):
    d = a.open_drawer()
    sale = sold(a)
    a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5700})
    r = m.refund(sale["id"], [(1, 1)])
    assert r.status_code == 409 and r.json()["error"] == "no_open_drawer"


def test_refund_paid_from_other_register(a, m):
    a.open_drawer(5000, register="R1")
    a.open_drawer(5000, register="R2")
    sale = sold(a, register="R1")
    rf = m.refund(sale["id"], [(1, 1)], register="R2").json()
    assert rf["register_id"] == a.register_id("R2")
    assert a.drawer("R1")["expected_cents"] == 5700
    assert a.drawer("R2")["expected_cents"] == 5000 - 250


def test_cannot_pay_out_more_cash_than_drawer_holds(a, m, app):
    a.open_drawer(5000, register="R1")
    a.open_drawer(100, register="R2")
    sale = sold(a, register="R1")
    r = m.refund(sale["id"], [(1, 2), (2, 1)], register="R2")  # needs 7.00, has 1.00
    assert r.status_code == 409 and r.json()["error"] == "insufficient_drawer_cash"
    assert n_refunds(app) == 0
    with app.state.session_factory() as s:  # allocation counters rolled back too
        assert sum(x.refunded_quantity for x in s.scalars(select(RefundAllocation))) == 0


def test_close_reconciles_refunds(a, m):
    d = a.open_drawer(5000)
    sale = sold(a)                    # +7.00
    m.refund(sale["id"], [(1, 1)])    # -2.50
    c = a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5450}).json()
    assert c["expected_cents"] == 5000 + 700 - 250
    assert c["variance_cents"] == 0
    assert (c["refund_count"], c["cash_refunds_cents"]) == (1, 250)


def test_close_detects_refund_ledger_mismatch(a, m, app):
    d = a.open_drawer(5000)
    sale = sold(a)
    m.refund(sale["id"], [(1, 1)])
    with app.state.session_factory.begin() as s:
        s.execute(update(DrawerSession).where(DrawerSession.id == d["id"])
                  .values(cash_refunds_cents=0))
    r = a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5450})
    assert r.status_code == 500 and r.json()["error"] == "ledger_mismatch"
    assert a.drawer()["status"] == "open"


# --------------------------------------------------------------------- tenancy


def test_refunds_are_tenant_scoped(a, b, m, app):
    a.open_drawer()
    sale = sold(a)
    bm = b.as_role("manager")
    b.open_drawer()
    r = bm.post(f"/api/v1/sales/{sale['id']}/refunds",
                {"register_id": b.register_id(), "lines": [{"line_no": 1, "quantity": 1}],
                 "reason": "võõras"}, headers={"Idempotency-Key": "cross-tenant-refund"})
    assert r.status_code == 404
    rf = m.refund(sale["id"], [(1, 1)]).json()
    assert b.get(f"/api/v1/refunds/{rf['id']}").status_code == 404
    assert b.get(f"/api/v1/refunds/{rf['id']}/receipt.txt").status_code == 404
    assert b.get(f"/api/v1/sales/{sale['id']}/refunds").status_code == 404
    # A's manager cannot pay out of B's register either
    r = m.post(f"/api/v1/sales/{sale['id']}/refunds",
               {"register_id": b.register_id(), "lines": [{"line_no": 1, "quantity": 1}],
                "reason": "võõras kassa"}, headers={"Idempotency-Key": "cross-reg-refund"})
    assert r.status_code == 404
    assert n_refunds(app) == 1


# ------------------------------------------------------------------ audit trail


def test_refund_outbox_event_and_original_event_unchanged(a, m, app):
    a.open_drawer()
    sale = sold(a)
    with app.state.session_factory() as s:
        sale_ev = s.scalar(select(OutboxEvent).where(OutboxEvent.type == "pos.sale.completed"))
        sale_ev_hash = sale_ev.payload_hash
    rf = m.refund(sale["id"], [(1, 1)]).json()
    with app.state.session_factory() as s:
        evs = s.scalars(select(OutboxEvent).order_by(OutboxEvent.occurred_at)).all()
    assert [e.type for e in evs].count("pos.refund.completed") == 1
    ev = next(e for e in evs if e.type == "pos.refund.completed")
    p = json.loads(ev.payload)
    assert p["original_sale_id"] == sale["id"]
    assert p["original_correlation_id"] == sale["correlation_id"]
    assert p["refund_id"] == rf["id"] and p["simulated"] is True
    assert ev.correlation_id == rf["correlation_id"]
    assert hashlib.sha256(ev.payload.encode()).hexdigest() == ev.payload_hash
    assert next(e for e in evs if e.type == "pos.sale.completed").payload_hash == sale_ev_hash


def test_refund_records_are_append_only(a, m, app):
    a.open_drawer()
    sale = sold(a)
    m.refund(sale["id"], [(1, 1)])
    with app.state.session_factory() as s:
        rf = s.scalar(select(Refund))
        rf.total_cents = 0
        with pytest.raises(AppendOnlyViolation):
            s.flush()
        s.rollback()
        s.delete(s.scalar(select(RefundLine)))
        with pytest.raises(AppendOnlyViolation):
            s.flush()
        s.rollback()
        s.scalar(select(Sale)).total_cents = 0
        with pytest.raises(AppendOnlyViolation):
            s.flush()


# --------------------------------------------------------------------- reports


def test_daily_report_includes_refunds_and_net(a, m):
    a.open_drawer(register="R1")
    a.open_drawer(register="R2")
    s1 = sold(a, register="R1")                        # 6.99 / VAT 136 / cash 7.00
    sold(a, [("RAAMAT", 1)], 1600, register="R2")      # 15.99 / VAT 132 / cash 16.00
    m.refund(s1["id"], [(1, 1)], register="R2")        # 1 of 2: VAT half_up(97/2)=49
    r = a.get("/api/v1/reports/daily", params={"business_date": "2026-03-10"}).json()
    assert r["sale_count"] == 2 and r["refund_count"] == 1
    assert (r["refund_total_cents"], r["refund_vat_cents"], r["cash_refunded_cents"]) == (250, 49, 250)
    assert r["net_total_cents"] == 699 + 1599 - 250
    assert r["net_vat_cents"] == 136 + 132 - 49
    assert r["net_cash_cents"] == 700 + 1600 - 250
    assert r["refund_vat_breakdown"] == [
        {"vat_rate_bp": 2400, "gross_cents": 250, "vat_cents": 49, "net_cents": 201}]
    reg2 = next(x for x in r["by_register"] if x["register_id"] == a.register_id("R2"))
    assert (reg2["refund_count"], reg2["cash_refunded_cents"]) == (1, 250)


def test_refund_on_later_day_counts_on_that_day(a, m, clock):
    a.open_drawer()
    sale = sold(a)
    clock.at = datetime(2026, 3, 11, 9, 0, tzinfo=timezone.utc)
    rf = m.refund(sale["id"], [(2, 1)]).json()
    assert rf["business_date"] == "2026-03-11"
    day1 = a.get("/api/v1/reports/daily", params={"business_date": "2026-03-10"}).json()
    day2 = a.get("/api/v1/reports/daily", params={"business_date": "2026-03-11"}).json()
    assert (day1["sale_count"], day1["refund_count"]) == (1, 0)
    assert (day2["sale_count"], day2["refund_count"]) == (0, 1)
    assert day2["net_total_cents"] == -199
