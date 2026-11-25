"""Sandbox terminal: a scriptable, in-memory stand-in for a real provider.

It models the *provider side* separately from the *answer the POS receives*,
so every hard case can be tested without hardware or money:

    script                       provider records        POS receives
    "approve"                    APPROVED                APPROVED
    "decline"                    DECLINED                DECLINED
    "timeout_after_approve"      APPROVED                TerminalTimeout (answer lost)
    "timeout_after_decline"      DECLINED                TerminalTimeout
    "offline"                    nothing                 TerminalUnavailable
    "pending_then_approve"       PENDING -> APPROVED     PENDING; get_status flips after N polls
    "pending_then_decline"       PENDING -> DECLINED     same

No network, no card data, no real money. Used by tests and demos only.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone

from .base import (
    Capabilities, CapabilityNotSupported, Health, PaymentRequest, RefundRequest, SettlementLine,
    Status, TerminalResult, TerminalTimeout, TerminalUnavailable,
)

SCRIPTS = {"approve", "decline", "timeout_after_approve", "timeout_after_decline", "offline",
           "pending_then_approve", "pending_then_decline"}


class SandboxTerminal:
    name = "sandbox"

    def __init__(self, capabilities: Capabilities | None = None, polls_until_final: int = 2):
        self._caps = capabilities or Capabilities(cancel_pending=True, refund=True, settlement_report=True)
        self._polls_until_final = polls_until_final
        self._lock = threading.Lock()
        self._attempts: dict[str, TerminalResult] = {}     # provider-side truth
        self._pending: dict[str, tuple[Status, int]] = {}   # attempt -> (final status, polls left)
        self._refunds: dict[str, TerminalResult] = {}
        self._script: list[str] = []
        self.charges = 0                                    # how many times money was taken
        self.offline_terminals: set[str] = set()

    # ------------------------------------------------------------ test hooks

    def script(self, *outcomes: str) -> None:
        for o in outcomes:
            if o not in SCRIPTS:
                raise ValueError(f"unknown script {o!r}")
        self._script.extend(outcomes)

    def _next(self) -> str:
        return self._script.pop(0) if self._script else "approve"

    # -------------------------------------------------------------- adapter

    def capabilities(self) -> Capabilities:
        return self._caps

    def health(self, terminal_id: str) -> Health:
        online = terminal_id not in self.offline_terminals
        return Health(terminal_id, online, "" if online else "terminal offline")

    def start_payment(self, req: PaymentRequest) -> TerminalResult:
        with self._lock:
            known = self._attempts.get(req.attempt_id)
            if known is not None:                            # rule 1: idempotent
                if known.amount_cents != req.amount_cents:
                    raise ValueError("attempt id reused with a different amount")
                return self._resolve_locked(req.attempt_id)
            if req.terminal_id in self.offline_terminals:
                raise TerminalUnavailable("terminal offline; request not sent")
            outcome = self._next()
            if outcome == "offline":
                raise TerminalUnavailable("connection refused before sending")
            now = datetime.now(timezone.utc)
            final = {"approve": Status.APPROVED, "timeout_after_approve": Status.APPROVED,
                     "pending_then_approve": Status.APPROVED}.get(outcome, Status.DECLINED)
            if outcome.startswith("pending_then"):
                self._pending[req.attempt_id] = (final, self._polls_until_final)
                status = Status.PENDING
            else:
                status = final
            res = TerminalResult(req.attempt_id, status, req.amount_cents,
                                 provider_ref=f"SBX-{req.attempt_id[:12]}", at=now, raw_status=status.value,
                                 auth_code="A1B2C3" if status == Status.APPROVED else None,
                                 card_brand="VISA", card_last4="4242")
            self._attempts[req.attempt_id] = res
            if status == Status.APPROVED:
                self.charges += 1
            if outcome.startswith("timeout_after"):
                raise TerminalTimeout("no answer from terminal (provider may have processed it)")
            return res

    def _resolve_locked(self, attempt_id: str) -> TerminalResult:
        res = self._attempts.get(attempt_id)
        if res is None:
            return TerminalResult(attempt_id, Status.NOT_FOUND, 0, raw_status="not_found")
        if res.status == Status.PENDING and attempt_id in self._pending:
            final, left = self._pending[attempt_id]
            if left <= 1:
                del self._pending[attempt_id]
                res = replace(res, status=final, raw_status=final.value,
                              auth_code="A1B2C3" if final == Status.APPROVED else None)
                self._attempts[attempt_id] = res
                if final == Status.APPROVED:
                    self.charges += 1
            else:
                self._pending[attempt_id] = (final, left - 1)
        return res

    def get_status(self, attempt_id: str) -> TerminalResult:
        with self._lock:
            return self._resolve_locked(attempt_id)

    def cancel(self, attempt_id: str) -> TerminalResult:
        if not self._caps.cancel_pending:
            raise CapabilityNotSupported("cancel")
        with self._lock:
            res = self._resolve_locked(attempt_id)
            if res.status != Status.PENDING:
                return res                                   # too late (or never existed): unchanged
            self._pending.pop(attempt_id, None)
            res = replace(res, status=Status.CANCELLED, raw_status="cancelled")
            self._attempts[attempt_id] = res
            return res

    def refund(self, req: RefundRequest) -> TerminalResult:
        if not self._caps.refund:
            raise CapabilityNotSupported("refund")
        with self._lock:
            if req.refund_id in self._refunds:
                return self._refunds[req.refund_id]
            orig = self._attempts.get(req.original_attempt_id)
            if orig is None or orig.status != Status.APPROVED:
                raise ValueError("can only refund an approved attempt")
            refunded = sum(r.amount_cents for r in self._refunds.values()
                           if r.provider_ref == orig.provider_ref)
            if req.amount_cents <= 0 or refunded + req.amount_cents > orig.amount_cents:
                raise ValueError("refund exceeds the approved amount")
            res = TerminalResult(req.refund_id, Status.APPROVED, req.amount_cents, provider_ref=orig.provider_ref,
                                 at=datetime.now(timezone.utc), raw_status="refunded")
            self._refunds[req.refund_id] = res
            return res

    def settlements(self, day: str) -> list[SettlementLine]:
        if not self._caps.settlement_report:
            raise CapabilityNotSupported("settlement_report")
        with self._lock:
            return [SettlementLine(r.provider_ref or "", r.attempt_id, r.amount_cents, r.status, day)
                    for r in self._attempts.values() if r.status == Status.APPROVED]
