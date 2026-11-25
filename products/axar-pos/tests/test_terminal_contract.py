"""Contract every card terminal adapter must pass (#25, ADR-0014).

Add a real adapter (Revolut, Nexi, Worldline, ...) to ADAPTERS with a factory
that drives its SANDBOX; the same tests then prove it honours the POS rules.
Today only the in-memory sandbox exists - no provider is connected."""

import threading
import uuid

import pytest

from axar_pos.terminals import (
    Capabilities, CapabilityNotSupported, PaymentRequest, RefundRequest, SandboxTerminal, Status,
    TerminalAdapter, TerminalTimeout, TerminalUnavailable, get_adapter,
)
from axar_pos.terminals import flow
from axar_pos.terminals.base import contains_pan_like

# name -> (factory, scripter). The scripter forces the next outcome on the
# adapter's sandbox; a real adapter's test harness supplies its own.
ADAPTERS = {
    "sandbox": (lambda: SandboxTerminal(), lambda a, *o: a.script(*o)),
}


@pytest.fixture(params=sorted(ADAPTERS))
def adapter(request):
    factory, scripter = ADAPTERS[request.param]
    a = factory()
    a._test_script = lambda *o: scripter(a, *o)   # noqa: SLF001
    return a


def req(amount=1250, terminal="T1"):
    return PaymentRequest(attempt_id=uuid.uuid4().hex, terminal_id=terminal, amount_cents=amount)


# --------------------------------------------------------------- the contract


def test_implements_the_protocol_and_requires_status_query(adapter):
    assert isinstance(adapter, TerminalAdapter)
    assert adapter.capabilities().status_query is True


def test_same_attempt_id_never_charges_twice(adapter):
    r = req()
    first = adapter.start_payment(r)
    again = adapter.start_payment(r)
    assert first.status == again.status == Status.APPROVED and first.provider_ref == again.provider_ref
    assert adapter.charges == 1


def test_parallel_double_tap_with_one_attempt_id_charges_once(adapter):
    r = req()
    with_results = []
    ts = [threading.Thread(target=lambda: with_results.append(adapter.start_payment(r))) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len({x.provider_ref for x in with_results}) == 1 and adapter.charges == 1


def test_reusing_an_attempt_id_for_another_amount_is_refused(adapter):
    r = req(1000)
    adapter.start_payment(r)
    with pytest.raises(ValueError):
        adapter.start_payment(PaymentRequest(r.attempt_id, r.terminal_id, 2000))


def test_lost_answer_is_resolved_by_status_query_not_guessed(adapter):
    adapter._test_script("timeout_after_approve")
    r = req()
    with pytest.raises(TerminalTimeout):
        adapter.start_payment(r)
    assert adapter.get_status(r.attempt_id).status == Status.APPROVED     # money WAS taken
    adapter._test_script("timeout_after_decline")
    r2 = req()
    with pytest.raises(TerminalTimeout):
        adapter.start_payment(r2)
    assert adapter.get_status(r2.attempt_id).status == Status.DECLINED


def test_not_sent_means_not_found_and_nothing_charged(adapter):
    adapter._test_script("offline")
    r = req()
    with pytest.raises(TerminalUnavailable):
        adapter.start_payment(r)
    assert adapter.get_status(r.attempt_id).status == Status.NOT_FOUND and adapter.charges == 0


def test_decline_takes_no_money(adapter):
    adapter._test_script("decline")
    assert adapter.start_payment(req()).status == Status.DECLINED and adapter.charges == 0


def test_pending_becomes_final_through_status(adapter):
    adapter._test_script("pending_then_approve")
    r = req()
    assert adapter.start_payment(r).status == Status.PENDING
    statuses = [adapter.get_status(r.attempt_id).status for _ in range(5)]
    assert statuses[-1] == Status.APPROVED and adapter.charges == 1


def test_refund_is_idempotent_and_never_exceeds_the_approved_amount(adapter):
    if not adapter.capabilities().refund:
        with pytest.raises(CapabilityNotSupported):
            adapter.refund(RefundRequest(uuid.uuid4().hex, uuid.uuid4().hex, 1))
        return
    r = req(1000)
    adapter.start_payment(r)
    rf = RefundRequest(uuid.uuid4().hex, r.attempt_id, 600)
    assert adapter.refund(rf) == adapter.refund(rf)                       # same refund id = one refund
    with pytest.raises(ValueError):
        adapter.refund(RefundRequest(uuid.uuid4().hex, r.attempt_id, 401))  # 600 + 401 > 1000
    adapter.refund(RefundRequest(uuid.uuid4().hex, r.attempt_id, 400))    # exactly the rest


def test_unsupported_operations_raise_instead_of_being_emulated():
    a = SandboxTerminal(Capabilities(cancel_pending=False, refund=False, settlement_report=False))
    for call in (lambda: a.cancel("x" * 10), lambda: a.refund(RefundRequest("r" * 10, "x" * 10, 1)),
                 lambda: a.settlements("2026-11-25")):
        with pytest.raises(CapabilityNotSupported):
            call()


def test_results_never_carry_card_numbers(adapter):
    for outcome in ("approve", "decline"):
        adapter._test_script(outcome)
        res = adapter.start_payment(req())
        assert not contains_pan_like(res)
        assert res.card_last4 is None or len(res.card_last4) == 4


def test_request_validation():
    with pytest.raises(ValueError):
        PaymentRequest("short", "T1", 100)
    with pytest.raises(ValueError):
        PaymentRequest(uuid.uuid4().hex, "T1", 0)


# ------------------------------------------------------------------ the flow


def test_flow_lost_answer_is_unknown_then_resolved_and_blocks_a_second_charge():
    a = SandboxTerminal()
    a.script("timeout_after_approve")
    r = req()
    out = flow.charge(a, r)
    assert out.pos_status == "unknown"                    # never "declined" on a lost answer
    assert flow.can_retry(a, r.attempt_id) is False       # money was taken: no new attempt
    assert flow.resolve(a, r.attempt_id).pos_status == "captured"
    assert a.charges == 1


def test_flow_not_sent_allows_a_new_attempt():
    a = SandboxTerminal()
    a.script("offline")
    r = req()
    assert flow.charge(a, r).pos_status == "not_sent"
    assert flow.can_retry(a, r.attempt_id) is True
    assert flow.charge(a, req()).pos_status == "captured" and a.charges == 1


def test_flow_pending_resolves_or_stays_unknown_when_provider_is_slow():
    a = SandboxTerminal(polls_until_final=3)
    a.script("pending_then_decline")
    r = req()
    assert flow.charge(a, r).pos_status == "unknown"
    assert flow.resolve(a, r.attempt_id, max_polls=1).pos_status == "unknown"   # still pending: no guess
    assert flow.resolve(a, r.attempt_id, max_polls=5).pos_status == "declined"


def test_flow_refuses_adapters_without_status_query():
    a = SandboxTerminal(Capabilities(status_query=False))
    with pytest.raises(CapabilityNotSupported):
        flow.charge(a, req())


def test_flow_refuses_card_number_like_data():
    from dataclasses import replace

    class Leaky(SandboxTerminal):
        def start_payment(self, r):
            return replace(super().start_payment(r), raw_status="card 4111111111111111")

    with pytest.raises(RuntimeError, match="card-number-like"):
        flow.charge(Leaky(), req())


def test_registry():
    assert isinstance(get_adapter("sandbox"), SandboxTerminal)
    with pytest.raises(ValueError, match="no terminal adapter"):
        get_adapter("revolut")                              # not connected until the owner chooses
