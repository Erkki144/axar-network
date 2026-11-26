"""Recorded sandbox terminal: the SIMULATED provider the POS uses today.

Same contract as every real adapter (``base.TerminalAdapter``; passes
``tests/test_terminal_contract.py``), but the provider-side truth is stored in
``mock_terminal_txns`` so it survives restarts and can be asked later
("what happened to attempt X?"), like a real provider's API.

The provider is a separate system: its record is written in ITS OWN
transaction (a separate database session), never in the POS transaction.
So a POS crash after the terminal approved still leaves the approval at the
"provider", and reconciliation finds it.

No network, no card data, no real money.

``script`` decides what the simulated customer / provider does:

    approve                provider APPROVED, POS gets the answer
    decline                provider DECLINED
    timeout_after_approve  provider APPROVED, the answer to the POS is lost
    offline                the request never reaches the provider
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import MockTerminalTxn
from .base import (
    Capabilities, CapabilityNotSupported, Health, PaymentRequest, RefundRequest, SettlementLine, Status,
    TerminalResult, TerminalTimeout, TerminalUnavailable,
)

SCRIPTS = {"approve", "decline", "timeout_after_approve", "offline"}


class RecordedSandboxTerminal:
    name = "recorded-sandbox"

    def __init__(self, engine: Engine, tenant_id: str, script: str = "approve"):
        if script not in SCRIPTS:
            raise ValueError(f"unknown script {script!r}")
        self.engine, self.tenant_id, self.script = engine, tenant_id, script

    def _session(self) -> Session:
        s = Session(bind=self.engine, expire_on_commit=False)
        s.begin()
        if self.engine.dialect.name == "postgresql":   # row-level security of the provider's own connection
            s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": self.tenant_id})
        return s

    # -------------------------------------------------------------- adapter

    def capabilities(self) -> Capabilities:
        # status_query is what makes "unknown" resolvable (contract rule 2).
        return Capabilities(purchase=True, partial_amount=True, status_query=True, cancel_pending=False,
                            refund=False, settlement_report=True)

    def health(self, terminal_id: str) -> Health:
        return Health(terminal_id, self.script != "offline", "" if self.script != "offline" else "terminal offline")

    def _result(self, row: MockTerminalTxn) -> TerminalResult:
        status = Status.APPROVED if row.result == "approved" else Status.DECLINED
        return TerminalResult(row.payment_id, status, row.amount_cents, provider_ref=f"SBX-{row.payment_id[:8]}",
                              auth_code="SIM001" if status == Status.APPROVED else None, card_brand="SIMULATED",
                              card_last4="0000", at=row.created_at, raw_status=row.result)

    def start_payment(self, req: PaymentRequest) -> TerminalResult:
        if self.script == "offline":
            raise TerminalUnavailable("terminal offline; request not sent")
        s = self._session()
        try:
            row = s.get(MockTerminalTxn, req.attempt_id)
            if row is None:                               # rule 1: the attempt id is the idempotency key
                row = MockTerminalTxn(payment_id=req.attempt_id, tenant_id=self.tenant_id,
                                      amount_cents=req.amount_cents,
                                      result="declined" if self.script == "decline" else "approved",
                                      created_at=datetime.now(timezone.utc))
                s.add(row)
                try:
                    s.commit()
                except IntegrityError:                    # the same attempt raced: read the winner
                    s.rollback()
                    s.begin()
                    row = s.get(MockTerminalTxn, req.attempt_id)
            elif row.amount_cents != req.amount_cents:
                raise ValueError("attempt id reused with a different amount")
            res = self._result(row)
        finally:
            s.close()
        if self.script == "timeout_after_approve":
            raise TerminalTimeout("no answer from the terminal (the provider may have processed it)")
        return res

    def get_status(self, attempt_id: str) -> TerminalResult:
        s = self._session()
        try:
            row = s.get(MockTerminalTxn, attempt_id)
            return (self._result(row) if row is not None
                    else TerminalResult(attempt_id, Status.NOT_FOUND, 0, raw_status="not_found"))
        finally:
            s.close()

    def cancel(self, attempt_id: str) -> TerminalResult:
        raise CapabilityNotSupported("cancel")

    def refund(self, req: RefundRequest) -> TerminalResult:
        raise CapabilityNotSupported("refund")     # card refunds come with #48

    def settlements(self, day: str) -> list[SettlementLine]:
        s = self._session()
        try:
            rows = s.scalars(select(MockTerminalTxn).where(MockTerminalTxn.tenant_id == self.tenant_id))
            return [SettlementLine(f"SBX-{r.payment_id[:8]}", r.payment_id, r.amount_cents,
                                   Status.APPROVED if r.result == "approved" else Status.DECLINED,
                                   r.created_at.date().isoformat())
                    for r in rows if r.created_at.date().isoformat() == day]
        finally:
            s.close()
