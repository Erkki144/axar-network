"""Card payment flow on top of any TerminalAdapter - the only code the POS
calls. It turns provider behaviour into the POS's payment states
(captured / declined / unknown) and enforces the never-charge-twice rules.

    charge(...)   start one attempt; a lost answer -> "unknown", never "declined"
    resolve(...)  ask the provider what really happened to an unknown attempt
    can_retry(..) a NEW attempt for the same money is allowed only when the
                  previous one is provably moneyless (declined/cancelled/not_found)
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .base import (
    FINAL, NO_MONEY, CapabilityNotSupported, PaymentRequest, Status, TerminalAdapter, TerminalResult,
    TerminalTimeout, TerminalUnavailable, redact_pans,
)


@dataclass(frozen=True)
class Outcome:
    attempt_id: str
    pos_status: str                 # captured | declined | unknown | not_sent
    result: TerminalResult | None
    detail: str = ""


REDACTED_NOTE = "card-number-like data from the provider was redacted (alert: check the adapter)"


def _to_pos(res: TerminalResult) -> str:
    if res.status == Status.APPROVED:
        return "captured"
    if res.status in NO_MONEY:
        return "declined"
    return "unknown"                # PENDING


def charge(adapter: TerminalAdapter, req: PaymentRequest) -> Outcome:
    if not adapter.capabilities().status_query:
        # Rule 2 cannot be honoured without status queries: refuse such adapters.
        raise CapabilityNotSupported("status_query is required for card payments")
    try:
        res = adapter.start_payment(req)
    except TerminalUnavailable as e:
        return Outcome(req.attempt_id, "not_sent", None, str(e))
    except TerminalTimeout as e:
        return Outcome(req.attempt_id, "unknown", None, str(e))
    return _outcome(req.attempt_id, res)


def _outcome(attempt_id: str, res: TerminalResult) -> Outcome:
    """Never refuse a provider answer (the money may already be taken): mask
    card-number-like data, keep the payment state, flag it in ``detail``."""
    res, redacted = redact_pans(res)
    return Outcome(attempt_id, _to_pos(res), res, REDACTED_NOTE if redacted else "")


def resolve(adapter: TerminalAdapter, attempt_id: str, *, max_polls: int = 10,
            interval_s: float = 0.0) -> Outcome:
    """Poll until the provider gives a final answer (bounded). Still pending
    after ``max_polls`` -> stays "unknown" (a person decides; never guess)."""
    res = None
    for i in range(max_polls):
        res = adapter.get_status(attempt_id)
        if res.status in FINAL:
            return _outcome(attempt_id, res)
        if interval_s and i + 1 < max_polls:
            time.sleep(interval_s)
    res, _ = redact_pans(res) if res is not None else (None, False)
    return Outcome(attempt_id, "unknown", res, "provider still pending")


def can_retry(adapter: TerminalAdapter, previous_attempt_id: str) -> bool:
    """A new attempt for the same money only if the previous one took none."""
    return adapter.get_status(previous_attempt_id).status in NO_MONEY
