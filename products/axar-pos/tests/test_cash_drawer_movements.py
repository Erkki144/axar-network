"""#68 cash custody movements: physical drawer != sales or payment ledger."""

import uuid
import pytest
from sqlalchemy import select

from axar_pos.models import DrawerCashMovement, OutboxEvent, AppendOnlyViolation


def movement(a, drawer_id, direction="out", amount_cents=1000, *, key=None,
             reason_code=None, reason_text=None):
    return a.post(f"/api/v1/drawer/{drawer_id}/movements", {
        "direction": direction, "amount_cents": amount_cents,
        "reason_code": reason_code or ("SAFE_DROP" if direction == "out" else "FLOAT_TOPUP"),
        "reason_text": reason_text or ("Seifi üleandmine" if direction == "out" else "Vahetusraha lisamine"),
    }, headers={"Idempotency-Key": key or str(uuid.uuid4())})


def test_cash_safe_drop_changes_expected_not_sales(a):
    d = a.open_drawer(10000)
    a.sell([("KOHV", 2)], 500)  # 5.00 cash
    out = movement(a, d["id"], "out", 4000)
    assert out.status_code == 201, out.text
    assert out.json()["direction"] == "out"
    assert out.json()["amount_cents"] == 4000
    cur = a.drawer()
    assert cur["cash_in_cents"] == 0
    assert cur["cash_out_cents"] == 4000
    assert cur["expected_cents"] == 6500  # 100 + 5 - 40 euros
    rep = a.get("/api/v1/reports/daily").json()
    assert rep["sale_count"] == 1
    assert rep["total_cents"] == 500
    closed = a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 6500})
    assert closed.status_code == 200, closed.text
    assert closed.json()["variance_cents"] == 0


def test_cash_in_and_out_ledger_and_trace(a):
    d = a.open_drawer(1000)
    i = movement(a, d["id"], "in", 5000)
    o = movement(a, d["id"], "out", 2500)
    assert (i.status_code, o.status_code) == (201, 201)
    assert a.drawer()["expected_cents"] == 3500
    rows = a.get(f"/api/v1/drawer/{d['id']}/movements").json()
    assert len(rows) == 2
    assert [(row["direction"], row["amount_cents"]) for row in rows] == [("in", 5000), ("out", 2500)]
    assert [row["sequence_no"] for row in rows] == [1, 2]  # fixed clock still has ordered cash custody
    # Pure cash custody events cannot mutate food sales or VAT totals.
    daily = a.get("/api/v1/reports/daily").json()
    assert daily["total_cents"] == 0
    assert daily["vat_cents"] == 0
    with a.c.app.state.session_factory() as sess:
        etypes = [x.type for x in sess.scalars(select(OutboxEvent).where(
            OutboxEvent.tenant_id == "t-a"))]
        assert etypes.count("drawer.cash.moved") == 2


def test_replay_with_same_key_is_identical_and_never_double_debits(a):
    d = a.open_drawer(10000)
    key = "cash-safe-drop-00001"
    first = movement(a, d["id"], "out", 3000, key=key)
    second = movement(a, d["id"], "out", 3000, key=key)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert a.drawer()["cash_out_cents"] == 3000
    bad = movement(a, d["id"], "out", 4000, key=key)
    assert bad.status_code == 409
    assert bad.json()["error"] == "idempotency_key_reused"
    assert len(a.get(f"/api/v1/drawer/{d['id']}/movements").json()) == 1


def test_closed_drawer_rejects_cash_out(a):
    d = a.open_drawer(5000)
    assert a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5000}).status_code == 200
    r = movement(a, d["id"], "out", 300)
    assert r.status_code == 409
    assert a.drawer() is None
    assert a.get(f"/api/v1/drawer/{d['id']}/movements").json() == []


def test_no_negative_physical_cash(a):
    d = a.open_drawer(2000)
    bad = movement(a, d["id"], "out", 2001)
    assert bad.status_code == 409
    assert bad.json()["error"] == "insufficient_drawer_cash"
    assert a.drawer()["expected_cents"] == 2000
    assert a.get(f"/api/v1/drawer/{d['id']}/movements").json() == []


def test_strict_input_and_tenant_isolation(a, b):
    d = a.open_drawer(5000)
    for invalid in [0, -1, 1.5, "100"]:
        bad = movement(a, d["id"], "out", invalid)
        assert bad.status_code == 422, (invalid, bad.text)
    assert b.get(f"/api/v1/drawer/{d['id']}/movements").status_code == 404
    assert b.post(f"/api/v1/drawer/{d['id']}/movements", {
        "direction": "out", "amount_cents": 300,
        "reason_code": "SAFE_DROP", "reason_text": "võõras kassa",
    }, headers={"Idempotency-Key": str(uuid.uuid4())}).status_code == 404


def test_cash_movement_cannot_be_mutated(a):
    d = a.open_drawer(5000)
    data = movement(a, d["id"], "out", 500).json()
    with a.c.app.state.session_factory() as s:
        row = s.get(DrawerCashMovement, data["id"])
        row.amount_cents = 999
        with pytest.raises(AppendOnlyViolation):
            s.flush()


def test_open_and_close_without_movements_unchanged(a):
    d = a.open_drawer(5000)
    assert a.drawer()["cash_in_cents"] == 0
    assert a.drawer()["cash_out_cents"] == 0
    close = a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5000})
    assert close.status_code == 200, close.text
    assert close.json()["expected_cents"] == 5000


def test_drawer_close_detects_tampered_movement_counter(a):
    from sqlalchemy import update
    from axar_pos.models import DrawerSession

    d = a.open_drawer(5000)
    assert movement(a, d["id"], "out", 2000).status_code == 201
    with a.c.app.state.session_factory() as sess:
        sess.execute(update(DrawerSession).where(DrawerSession.id == d["id"])
                     .values(cash_out_cents=1000))
        sess.commit()
    bad = a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 4000})
    assert bad.status_code == 500, bad.text  # invariant corruption, not a user conflict
    assert bad.json()["error"] == "ledger_mismatch"
    assert len(a.get(f"/api/v1/drawer/{d['id']}/movements").json()) == 1


# ------------------------------------------------- integration with #60 (R1)

def _r1(app):
    from axar_pos import reconcile

    with app.state.session_factory() as s:
        return next(c for c in reconcile.check(s, "t-a")["checks"] if c["check"] == "R1.drawer_cash")


def test_r1_stays_green_with_a_safe_drop_and_a_float_top_up(a, app):
    """K2 (review 26.11): R1 must count non-sale cash, not call it a mismatch."""
    d = a.open_drawer(10000)
    a.sell([("KOHV", 2)], 500)
    assert movement(a, d["id"], "in", 2000).status_code == 201
    assert movement(a, d["id"], "out", 4000).status_code == 201
    assert _r1(app)["status"] == "ok"
    assert a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 8500}).status_code == 200
    r1 = _r1(app)
    assert r1["status"] == "ok", r1["problems"]


def test_r1_names_a_cash_movement_changed_behind_the_application(a, app):
    from sqlalchemy import text

    d = a.open_drawer(10000)
    assert movement(a, d["id"], "out", 4000).status_code == 201
    with app.state.session_factory.begin() as s:   # someone lowers the drawer's running cash-out
        s.execute(text("UPDATE drawer_sessions SET cash_out_cents = 1000 WHERE id = :i"), {"i": d["id"]})
    r1 = _r1(app)
    assert r1["status"] == "fail" and r1["problems"][0]["what"] == "cash out (non-sale)"


def test_drawer_cash_moved_events_match_their_committed_schema(a, app):
    """K3 (review 26.11): every emitted event has a contract."""
    import json

    from test_event_contracts import validator

    d = a.open_drawer(10000)
    assert movement(a, d["id"], "in", 1000).status_code == 201
    assert movement(a, d["id"], "out", 500).status_code == 201
    with app.state.session_factory() as s:
        evs = list(s.scalars(select(OutboxEvent).where(OutboxEvent.type == "drawer.cash.moved")))
    assert len(evs) == 2
    v = validator("drawer.cash.moved.v1.schema.json")
    for e in evs:
        assert [x.message for x in v.iter_errors(json.loads(e.payload))] == []
