"""#60 P0-3: card tenders go through the provider-neutral terminal layer
(``terminals.flow`` + an adapter), not through code inside ``payments.py``.

The provider is still SIMULATED (``terminals/recorded.py``): no network, no
card data, no money. What these tests prove is the ORDER of operations a real
provider needs:

1. the attempt is recorded (``unknown``, amount reserved) BEFORE the terminal
   is asked, so a crash never loses an attempt that may have taken money;
2. the terminal is asked OUTSIDE any POS transaction, and the provider keeps
   its own record (a separate transaction);
3. a lost answer is resolved by asking the provider; "never received" means
   no money; a completing tender checks the drawer BEFORE any money is taken.
"""

import uuid

import pytest
from pathlib import Path

from sqlalchemy import select

from axar_pos import payments
from axar_pos.models import MockTerminalTxn, Payment
from axar_pos.terminals import PaymentRequest, Status, TerminalTimeout, TerminalUnavailable
from axar_pos.terminals.recorded import RecordedSandboxTerminal

from test_payments import bill, open_bill, pay

SRC = Path(__file__).resolve().parents[1] / "axar_pos" / "payments.py"


def provider_rows(app):
    with app.state.session_factory() as s:
        return {r.payment_id: r.result for r in s.scalars(select(MockTerminalTxn))}


def test_payments_module_no_longer_fakes_the_provider_itself():
    src = SRC.read_text(encoding="utf-8")
    assert "MockTerminalTxn" not in src and "flow.charge(" in src and "flow.resolve(" in src


def test_recorded_sandbox_honours_the_terminal_rules(app):
    eng = app.state.engine
    a = RecordedSandboxTerminal(eng, "t-a", "approve")
    r = PaymentRequest(attempt_id=uuid.uuid4().hex, terminal_id="R1", amount_cents=500)
    first, again = a.start_payment(r), a.start_payment(r)              # rule 1: attempt id = idempotency key
    assert first.status == again.status == Status.APPROVED and len(provider_rows(app)) == 1
    lost = PaymentRequest(attempt_id=uuid.uuid4().hex, terminal_id="R1", amount_cents=300)
    try:
        RecordedSandboxTerminal(eng, "t-a", "timeout_after_approve").start_payment(lost)
        raise AssertionError("expected a lost answer")
    except TerminalTimeout:
        pass
    assert a.get_status(lost.attempt_id).status == Status.APPROVED     # rule 2: ask, never guess
    never = PaymentRequest(attempt_id=uuid.uuid4().hex, terminal_id="R1", amount_cents=300)
    try:
        RecordedSandboxTerminal(eng, "t-a", "offline").start_payment(never)
        raise AssertionError("expected not sent")
    except TerminalUnavailable:
        pass
    assert a.get_status(never.attempt_id).status == Status.NOT_FOUND   # rule 3: nothing charged
    res = a.start_payment(PaymentRequest(attempt_id=uuid.uuid4().hex, terminal_id="R1", amount_cents=100))
    assert res.card_last4 == "0000" and res.card_brand == "SIMULATED"   # rule 5: no card data


def test_terminal_offline_declines_without_any_provider_record(a, app):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    r = pay(a, o["id"], "card", 300, 500, simulate="offline")
    assert r.status_code == 201 and r.json()["payment"]["status"] == "declined"
    assert provider_rows(app) == {} and bill(a, o["id"])["remaining_cents"] == 500


def test_lost_answer_is_unknown_then_resolved_through_the_flow(a, app):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    p = pay(a, o["id"], "card", 500, 500, simulate="timeout").json()["payment"]
    assert p["status"] == "unknown" and provider_rows(app) == {p["id"]: "approved"}   # provider took it
    assert bill(a, o["id"])["sale_id"] is None
    r = a.post(f"/api/v1/payments/{p['id']}/reconcile")
    assert r.status_code == 200 and r.json()["status"] == "paid" and r.json()["sale_id"]


def test_crash_before_the_terminal_answered_resolves_to_no_money(a, app):
    """Phase 1 committed (attempt reserved), then the POS died before the
    terminal was asked: the provider never saw it -> declined, bill open."""
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    from axar_pos.models import Order

    reg = a.register_id()
    with app.state.session_factory() as s:
        order = s.get(Order, o["id"])
        p = payments._new_payment(order, method="card", status="unknown", amount=300,       # noqa: SLF001
                                  register_id=reg, key=str(uuid.uuid4()), req_hash="x",
                                  actor="kassa1", now=app.state.settings.clock())
        s.add(p)
        s.commit()
        pid = p.id
    assert bill(a, o["id"])["in_flight_cents"] == 300
    r = a.post(f"/api/v1/payments/{pid}/reconcile")
    assert r.status_code == 200 and r.json()["in_flight_cents"] == 0 and r.json()["remaining_cents"] == 500
    with app.state.session_factory() as s:
        assert s.get(Payment, pid).status == "declined"


def test_completing_card_tender_checks_the_drawer_before_taking_money(a, app):
    o = open_bill(a, [("KOHV", 2)])
    r = pay(a, o["id"], "card", 500, 500)
    assert r.status_code == 409 and r.json()["error"] == "no_open_drawer"
    assert provider_rows(app) == {}                                    # the terminal was never asked


def test_drawer_closed_during_the_terminal_call_records_the_money_and_can_be_settled(a, app, monkeypatch):
    """Rare race: the drawer closes while the customer taps the card. The
    approval is recorded (money taken), the receipt waits; reconciliation
    R11 names the bill, and /settle issues the receipt once a drawer is open."""
    d = a.open_drawer(0)
    o = open_bill(a, [("KOHV", 2)])
    real = RecordedSandboxTerminal.start_payment

    def close_drawer_first(self, req):
        assert a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 0}).status_code == 200
        return real(self, req)

    monkeypatch.setattr(RecordedSandboxTerminal, "start_payment", close_drawer_first)
    r = pay(a, o["id"], "card", 500, 500)
    assert r.status_code == 201 and r.json()["payment"]["status"] == "captured"
    b = r.json()["bill"]
    assert b["sale_id"] is None and b["remaining_cents"] == 0 and b["paid_cents"] == 500
    m = a.as_role("manager")
    rep = m.get("/api/v1/admin/reconciliation").json()
    r11 = next(c for c in rep["checks"] if c["check"] == "R11.orders_kitchen")
    assert r11["status"] == "fail" and r11["problems"][0]["reason"] == "bill fully covered by payments but no receipt issued"
    monkeypatch.undo()
    assert a.post(f"/api/v1/orders/{o['id']}/settle", {"register_id": a.register_id()}).status_code == 409
    a.open_drawer(0)
    r = a.post(f"/api/v1/orders/{o['id']}/settle", {"register_id": a.register_id()})
    assert r.status_code == 200 and r.json()["status"] == "paid" and r.json()["sale_id"]
    again = a.post(f"/api/v1/orders/{o['id']}/settle", {"register_id": a.register_id()})
    assert again.status_code == 200 and again.json()["sale_id"] == r.json()["sale_id"]   # idempotent
    assert m.get("/api/v1/admin/reconciliation").json()["ok"] is True


# ------------------------------------------- AION review (#65): live dispatch races
# A deterministic barrier pauses the dispatching request inside the terminal
# call while the test reconciles, pays again, and then lets it finish.

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from axar_pos.models import AuthEvent, Sale
from axar_pos.terminals import TerminalResult


def _wait_for_status(app, status, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with app.state.session_factory() as s:
            p = s.scalars(select(Payment).where(Payment.method == "card", Payment.status == status)).first()
            if p is not None:
                return p.id
        time.sleep(0.02)
    raise AssertionError(f"no card payment reached {status!r}")


def _paused_terminal(monkeypatch, *, reach_provider_first: bool):
    """Patch the simulated provider: the dispatcher stops at a barrier, either
    BEFORE its request reaches the provider or AFTER the provider approved but
    before the answer gets back."""
    entered, go = threading.Event(), threading.Event()
    real = RecordedSandboxTerminal.start_payment

    def paused(self, req):
        if reach_provider_first:
            res = real(self, req)
            entered.set()
            assert go.wait(20)
            return res
        entered.set()
        assert go.wait(20)
        return real(self, req)

    monkeypatch.setattr(RecordedSandboxTerminal, "start_payment", paused)
    return entered, go


def _audit(app, event):
    with app.state.session_factory() as s:
        return list(s.scalars(select(AuthEvent).where(AuthEvent.event == event)))


def test_reconcile_during_a_live_dispatch_cannot_release_the_reservation_or_charge_twice(a, app, monkeypatch, clock):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    entered, go = _paused_terminal(monkeypatch, reach_provider_first=False)
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(pay, a, o["id"], "card", 500, 500)
        assert entered.wait(10)
        pid = _wait_for_status(app, "sending")
        r = a.post(f"/api/v1/payments/{pid}/reconcile")                  # dispatcher still owns it
        assert r.status_code == 409 and r.json()["error"] == "dispatch_in_progress"
        assert bill(a, o["id"])["in_flight_cents"] == 500                  # reservation kept
        clock.at += timedelta(minutes=3)                                   # the dispatcher looks dead
        r = a.post(f"/api/v1/payments/{pid}/reconcile")                    # provider: not found -> cancelled there
        assert r.status_code == 200 and r.json()["remaining_cents"] == 500 and r.json()["in_flight_cents"] == 0
        assert provider_rows(app) == {pid: "cancelled"}                    # tombstone fences a late arrival
        monkeypatch.undo()
        second = pay(a, o["id"], "card", 500, 500)                         # the customer pays again
        assert second.status_code == 201 and second.json()["bill"]["status"] == "paid"
        go.set()                                                           # the original request finally arrives
        late = first.result(20)
    assert late.status_code == 201 and late.json()["payment"]["status"] == "declined"   # answered "cancelled"
    rows = provider_rows(app)
    assert sorted(rows.values()) == ["approved", "cancelled"]               # money taken exactly once
    with app.state.session_factory() as s:
        assert len(list(s.scalars(select(Sale)))) == 1
    assert a.as_role("manager").get("/api/v1/admin/reconciliation").json()["ok"] is True


def test_provider_approval_found_by_reconcile_is_kept_when_the_late_answer_arrives(a, app, monkeypatch, clock):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    entered, go = _paused_terminal(monkeypatch, reach_provider_first=True)
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(pay, a, o["id"], "card", 500, 500)
        assert entered.wait(10)
        pid = _wait_for_status(app, "sending")
        clock.at += timedelta(minutes=3)
        r = a.post(f"/api/v1/payments/{pid}/reconcile")                    # provider already approved
        assert r.status_code == 200 and r.json()["status"] == "paid" and r.json()["sale_id"]
        again = pay(a, o["id"], "card", 500, 500)                          # nothing left to pay
        assert again.status_code in (409, 422)
        go.set()
        late = first.result(20)
    assert late.json()["payment"]["status"] == "captured"                  # not discarded, not doubled
    assert list(provider_rows(app).values()) == ["approved"]
    with app.state.session_factory() as s:
        assert len(list(s.scalars(select(Sale)))) == 1


def test_a_late_approval_is_never_discarded_even_if_the_attempt_was_marked_declined(a, app, monkeypatch):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    entered, go = _paused_terminal(monkeypatch, reach_provider_first=True)
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(pay, a, o["id"], "card", 500, 500)
        assert entered.wait(10)
        pid = _wait_for_status(app, "sending")
        with app.state.admin_engine.begin() as c:                           # a wrong resolution behind our back
            from sqlalchemy import text
            c.execute(text("UPDATE payments SET status = 'declined' WHERE id = :p"), {"p": pid})
        go.set()
        late = first.result(20)
    assert late.json()["payment"]["status"] == "captured"                   # the money is recorded
    assert late.json()["bill"]["status"] == "paid"
    assert len(_audit(app, "card.late_approval")) == 1


def test_a_late_approval_that_overpays_is_recorded_and_named_by_reconciliation(a, app, monkeypatch):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    entered, go = _paused_terminal(monkeypatch, reach_provider_first=True)
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(pay, a, o["id"], "card", 500, 500)
        assert entered.wait(10)
        pid = _wait_for_status(app, "sending")
        with app.state.admin_engine.begin() as c:                           # wrongly declined, then paid in cash
            from sqlalchemy import text
            c.execute(text("UPDATE payments SET status = 'declined' WHERE id = :p"), {"p": pid})
        assert pay(a, o["id"], "cash", 500, 500, tendered_cents=500).status_code == 201
        go.set()
        late = first.result(20)
    assert late.json()["payment"]["status"] == "captured"                   # recorded, never dropped
    rep = a.as_role("manager").get("/api/v1/admin/reconciliation").json()
    r11 = next(c for c in rep["checks"] if c["check"] == "R11.orders_kitchen")
    assert not rep["ok"] and any("payments exceed the bill" in x["reason"] for x in r11["problems"])


@pytest.mark.parametrize("field", ["amount_cents", "attempt_id"])
def test_a_provider_answer_for_another_attempt_or_amount_is_not_trusted(a, app, monkeypatch, field):
    a.open_drawer()
    o = open_bill(a, [("KOHV", 2)])
    real = RecordedSandboxTerminal.start_payment

    def wrong(self, req):
        res = real(self, req)
        changes = {"amount_cents": res.amount_cents + 1} if field == "amount_cents" else {"attempt_id": "x" * 12}
        from dataclasses import replace
        return replace(res, **changes)

    monkeypatch.setattr(RecordedSandboxTerminal, "start_payment", wrong)
    r = pay(a, o["id"], "card", 300, 500)
    assert r.status_code == 201 and r.json()["payment"]["status"] == "unknown"   # never captured on bad data
    assert r.json()["bill"]["in_flight_cents"] == 300 and r.json()["bill"]["sale_id"] is None
    assert len(_audit(app, "card.provider_mismatch")) == 1
    monkeypatch.undo()
    pid = r.json()["payment"]["id"]
    r = a.post(f"/api/v1/payments/{pid}/reconcile")                        # the provider's own record decides
    assert r.status_code == 200 and r.json()["paid_cents"] == 300
