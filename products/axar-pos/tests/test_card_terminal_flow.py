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
