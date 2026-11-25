"""Provider-neutral card terminal interface (#25, ADR-0014).

Every concrete adapter (Revolut, Nexi, Worldline, ... - chosen later) maps
its provider API onto THIS contract and must pass
``tests/test_terminal_contract.py``. The POS never talks to a provider
directly and never sees card data.

Rules every adapter must honour (enforced by the contract tests):

1. **Our attempt id is the idempotency key.** ``start_payment`` with an
   attempt id the provider already knows returns that attempt's state; it
   never creates a second charge.
2. **A lost answer is UNKNOWN, not DECLINED.** A timeout / dropped connection
   after the request may have reached the provider raises
   ``TerminalTimeout``; the caller must resolve it with ``get_status`` and
   must not start a new attempt for the same money until it is resolved.
3. **Only a request that provably never reached the provider** (e.g. the
   terminal is offline) raises ``TerminalUnavailable`` - then nothing was
   charged and a new attempt is safe.
4. **Capabilities are honest.** An operation the provider/terminal does not
   support raises ``CapabilityNotSupported``; it is never emulated.
5. **No card data.** Results carry at most brand and last 4 digits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable


class Status(str, Enum):
    PENDING = "pending"        # sent to the terminal, customer has not finished
    APPROVED = "approved"      # authorised/captured by the provider
    DECLINED = "declined"      # definitive: no money taken
    CANCELLED = "cancelled"    # cancelled before approval: no money taken
    NOT_FOUND = "not_found"    # provider never received this attempt: no money taken


FINAL = {Status.APPROVED, Status.DECLINED, Status.CANCELLED, Status.NOT_FOUND}
NO_MONEY = {Status.DECLINED, Status.CANCELLED, Status.NOT_FOUND}


class TerminalError(Exception):
    pass


class TerminalTimeout(TerminalError):
    """The request MAY have reached the provider; outcome unknown."""


class TerminalUnavailable(TerminalError):
    """The request provably did NOT reach the provider; nothing was charged."""


class CapabilityNotSupported(TerminalError):
    pass


@dataclass(frozen=True)
class Capabilities:
    purchase: bool = True
    partial_amount: bool = True        # any amount <= bill (split tenders)
    status_query: bool = True          # REQUIRED for production use (rule 2)
    cancel_pending: bool = False
    refund: bool = False               # linked refund to an approved attempt
    offline_purchase: bool = False     # certified offline auth (ADR-0012 §4)
    tip_on_terminal: bool = False
    settlement_report: bool = False


@dataclass(frozen=True)
class PaymentRequest:
    attempt_id: str                    # OUR id; idempotency key at the provider
    terminal_id: str
    amount_cents: int
    currency: str = "EUR"
    reference: str = ""                # e.g. bill/receipt reference shown on the provider side

    def __post_init__(self):
        if self.amount_cents <= 0:
            raise ValueError("amount must be positive")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", self.attempt_id):
            raise ValueError("attempt_id must be 8-64 url-safe characters")


@dataclass(frozen=True)
class TerminalResult:
    attempt_id: str
    status: Status
    amount_cents: int
    provider_ref: str | None = None    # provider's transaction id (for reconciliation)
    auth_code: str | None = None
    card_brand: str | None = None
    card_last4: str | None = None      # at most the last 4 digits - never a PAN
    tip_cents: int = 0
    at: datetime | None = None
    raw_status: str | None = None      # provider's own status string, for audit

    def __post_init__(self):
        if self.card_last4 is not None and not re.fullmatch(r"\d{4}", self.card_last4):
            raise ValueError("card_last4 must be exactly 4 digits")


@dataclass(frozen=True)
class RefundRequest:
    refund_id: str                     # OUR id; idempotency key for the refund
    original_attempt_id: str
    amount_cents: int


@dataclass(frozen=True)
class Health:
    terminal_id: str
    online: bool
    detail: str = ""


@dataclass
class SettlementLine:
    provider_ref: str
    attempt_id: str
    amount_cents: int
    status: Status
    settled_on: str                    # ISO date


@runtime_checkable
class TerminalAdapter(Protocol):
    name: str

    def capabilities(self) -> Capabilities: ...

    def health(self, terminal_id: str) -> Health: ...

    def start_payment(self, req: PaymentRequest) -> TerminalResult:
        """Send the amount to the terminal. May return PENDING. Raises
        TerminalTimeout (outcome unknown) or TerminalUnavailable (not sent)."""

    def get_status(self, attempt_id: str) -> TerminalResult:
        """Authoritative state at the provider; NOT_FOUND if never received."""

    def cancel(self, attempt_id: str) -> TerminalResult: ...

    def refund(self, req: RefundRequest) -> TerminalResult: ...

    def settlements(self, day: str) -> list[SettlementLine]: ...


# 13-19 digits, optionally grouped by single spaces or hyphens ("4111 1111 ...").
PAN_CANDIDATE = re.compile(r"(?<![\d])(?:\d[ -]?){12,18}\d(?![\d])")
# Free-text fields that come from the PROVIDER. attempt_id is ours (validated in
# PaymentRequest) and card_last4 is validated to exactly 4 digits.
PROVIDER_TEXT_FIELDS = ("provider_ref", "auth_code", "card_brand", "raw_status")


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _pans(text: str) -> list[str]:
    out = []
    for m in PAN_CANDIDATE.finditer(text):
        digits = re.sub(r"[ -]", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            out.append(m.group())
    return out


def contains_pan_like(result: TerminalResult) -> bool:
    """True if a provider-supplied text field holds something that looks like a
    card number: 13-19 digits that also pass the Luhn check (as every real PAN
    does). Long numeric transaction ids and our own attempt ids are not flagged
    just for being long."""
    return any(isinstance(getattr(result, f), str) and _pans(getattr(result, f)) for f in PROVIDER_TEXT_FIELDS)


def redact_pans(result: TerminalResult) -> tuple[TerminalResult, bool]:
    """Mask card-number-like data in provider text fields; the payment state
    (status, amount, ids) is kept, so money already taken is still recorded."""
    from dataclasses import replace
    changes = {}
    for f in PROVIDER_TEXT_FIELDS:
        v = getattr(result, f)
        if isinstance(v, str):
            masked = v
            for pan in _pans(v):
                masked = masked.replace(pan, "[card number redacted]")
            if masked != v:
                changes[f] = masked
    return (replace(result, **changes), True) if changes else (result, False)
