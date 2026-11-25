"""Offline-first O1b, EDGE side: the sync agent (ADR-0012 §7).

Pushes pending ``sync_outbox`` rows of this device to the central server in
strict ``seq`` order, in batches. Reconnection is just "keep trying":

- network error / timeout / 5xx / 429 -> nothing changes locally, the batch
  is retried later with exponential backoff + jitter (capped);
- the server answers per entry: accepted / duplicate -> acked;
  quarantined -> acked by the server but needs a manager centrally (kept
  locally as ``quarantined``); gap -> resend from ``acked_through``;
  chain_broken / conflict -> the row is marked ``rejected`` and sync stops
  for a person to look at (never skipped silently);
- rows are never deleted - acked rows stay for audit (retention is O5).

Because every send carries the same entries and the server dedups by
device + seq + hash, a lost response or a crash between send and local update
is harmless: the next round resends and gets ``duplicate``.

``send(body) -> (http_status, json_or_None)`` is injected (HTTP client in
production, the test client + fault injection in tests).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .journal import entry_wire
from .models import DeviceJournalEntry, SyncOutbox

Send = Callable[[dict], "tuple[int, dict | None]"]


@dataclass
class RoundResult:
    state: str                  # idle | waiting | sent | retry | blocked
    sent: int = 0
    acked: int = 0
    quarantined: int = 0
    detail: str = ""


@dataclass
class SyncAgent:
    factory: Callable[[], Session]
    tenant_id: str
    device_id: str
    send: Send
    clock: Callable[[], datetime]
    batch_size: int = 50
    base_delay: timedelta = timedelta(seconds=2)
    max_delay: timedelta = timedelta(minutes=5)
    rng: random.Random = field(default_factory=random.Random)

    def _backoff(self, attempts: int) -> timedelta:
        delay = min(self.base_delay * (2 ** max(attempts - 1, 0)), self.max_delay)
        return delay * (0.5 + self.rng.random() / 2)     # nosec B311 - jitter only

    def status(self) -> dict:
        with self.factory() as s:
            rows = list(s.scalars(select(SyncOutbox).where(SyncOutbox.tenant_id == self.tenant_id,
                                                           SyncOutbox.device_id == self.device_id)))
        by = {}
        for r in rows:
            by[r.status] = by.get(r.status, 0) + 1
        pending = [r for r in rows if r.status == "pending"]
        return {"pending": by.get("pending", 0), "acked": by.get("acked", 0),
                "quarantined": by.get("quarantined", 0), "rejected": by.get("rejected", 0),
                "oldest_pending_at": min((r.created_at for r in pending), default=None)}

    def run_once(self) -> RoundResult:
        now = self.clock()
        with self.factory() as s:
            if s.scalar(select(SyncOutbox.entry_id).where(SyncOutbox.tenant_id == self.tenant_id,
                                                          SyncOutbox.device_id == self.device_id,
                                                          SyncOutbox.status == "rejected").limit(1)):
                return RoundResult("blocked", detail="a rejected entry needs a person (see last_error)")
            rows = list(s.scalars(select(SyncOutbox).where(
                SyncOutbox.tenant_id == self.tenant_id, SyncOutbox.device_id == self.device_id,
                SyncOutbox.status == "pending").order_by(SyncOutbox.seq).limit(self.batch_size)))
            if not rows:
                return RoundResult("idle")
            head = rows[0]
            if head.next_attempt_at is not None and head.next_attempt_at > now:
                return RoundResult("waiting", detail=f"next attempt at {head.next_attempt_at.isoformat()}")
            entries = {e.id: e for e in s.scalars(select(DeviceJournalEntry).where(
                DeviceJournalEntry.id.in_([r.entry_id for r in rows])))}
            body = {"device_id": self.device_id,
                    "entries": [{"entry": entry_wire(entries[r.entry_id]), "payload": r.payload,
                                 "payload_hash": r.payload_hash} for r in rows]}
        try:
            status, data = self.send(body)
        except Exception as e:  # network down, timeout, connection reset - outcome unknown, resend later
            return self._failed(rows, f"{type(e).__name__}: {e}"[:300], now)
        if status != 200 or not isinstance(data, dict):
            return self._failed(rows, f"HTTP {status}", now)
        return self._apply_answer(rows, data, now)

    def _failed(self, rows, error: str, now: datetime) -> RoundResult:
        with self.factory() as s:
            head = s.get(SyncOutbox, rows[0].entry_id)
            head.attempts += 1
            head.last_error = error
            head.next_attempt_at = now + self._backoff(head.attempts)
            s.commit()
        return RoundResult("retry", sent=len(rows), detail=error)

    def _apply_answer(self, rows, data: dict, now: datetime) -> RoundResult:
        res = RoundResult("sent", sent=len(rows))
        by_seq = {r["seq"]: r for r in data.get("results", [])}
        acked_through = int(data.get("acked_through", 0))
        with self.factory() as s:
            for row in rows:
                r = s.get(SyncOutbox, row.entry_id)
                ans = by_seq.get(r.seq)
                if ans and ans["status"] in ("conflict", "chain_broken"):
                    r.status, r.last_error = "rejected", f"{ans['status']}: {ans.get('reason', '')}"[:300]
                    res.state, res.detail = "blocked", r.last_error
                    break
                server_status = (ans or {}).get("server_status") or (ans or {}).get("status")
                if r.seq <= acked_through:
                    r.status = "quarantined" if server_status == "quarantined" else "acked"
                    r.server_result = (f"{server_status}: {(ans or {}).get('reason') or ''}")[:300]
                    r.acked_at, r.attempts, r.next_attempt_at, r.last_error = now, 0, None, None
                    res.acked += 1
                    res.quarantined += r.status == "quarantined"
            s.commit()
        return res

    def run_until_idle(self, max_rounds: int = 100) -> list[RoundResult]:
        out = []
        for _ in range(max_rounds):
            r = self.run_once()
            out.append(r)
            if r.state in ("idle", "waiting", "retry", "blocked"):
                break
        return out
