"""Transactional outbox v2, consumers with inbox / retries / dead letters, and
the realtime feed (ADR-0004).

Producing
---------
``emit(s, ...)`` queues an event on the session. It is written by a
``before_commit`` hook, i.e. inside the same transaction as the state change
and *after* every other write of that transaction. There the hook takes the
tenant's ``event`` counter row (``tenant_counters``) and stamps a gap-free
``recorded_seq``. Because that row lock is the LAST lock of every producing
transaction and is held until commit:

- sequence order == commit order within a tenant, so a reader that has seen
  ``seq = n`` never later finds a smaller one (cursor-safe);
- no deadlock cycle with the domain locks taken earlier (drawer, order, ...).

A rollback discards the queued events together with the state change.

Consuming (at-least-once, idempotent effect)
--------------------------------------------
``run_consumer`` takes a lease (fencing token) on ``event_consumers`` for
(consumer, tenant), reads events after the cursor in order and, per event,
in ONE transaction: inserts ``event_inbox`` (dedup), runs the handler, and
advances the cursor *only if the fencing token still matches* (a worker that
lost its lease cannot move the cursor). A failing handler is retried with
exponential backoff and jitter; after ``MAX_ATTEMPTS`` the event goes to
``event_dead_letters`` and the consumer continues. Nothing is dropped
silently; ``replay_dead_letter`` re-applies one explicitly.
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import event as sa_event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import DeadLetter, EventConsumer, InboxEntry, OutboxEvent

SOURCE = "axar-pos"
MAX_ATTEMPTS = 5
BACKOFF_BASE = timedelta(seconds=2)
BACKOFF_MAX = timedelta(minutes=5)
LEASE = timedelta(seconds=30)
_PENDING = "axar_outbox_pending"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def actor_of(s: Session) -> dict:
    """Actor recorded by the API identity dependency (``s.info['actor']``)."""
    return s.info.get("actor") or {"kind": "system"}


def emit(s: Session, *, tenant_id: str, type: str, payload: dict, aggregate_type: str, aggregate_id: str,
         aggregate_version: int, now: datetime, version: int = 1, correlation_id: str | None = None,
         causation_id: str | None = None, location_id: int | None = None) -> OutboxEvent:
    body = canonical(payload)
    ev = OutboxEvent(
        id=str(uuid.uuid4()), tenant_id=tenant_id, type=type, version=version, source=SOURCE, occurred_at=now,
        correlation_id=correlation_id or s.info.get("correlation_id") or str(uuid.uuid4()),
        payload=body, payload_hash=hashlib.sha256(body.encode()).hexdigest(),
        aggregate_type=aggregate_type, aggregate_id=str(aggregate_id), aggregate_version=aggregate_version,
        causation_id=causation_id, actor=canonical(actor_of(s)), location_id=location_id,
    )
    s.info.setdefault(_PENDING, []).append(ev)
    return ev


@sa_event.listens_for(Session, "before_commit")
def _write_pending_events(s: Session) -> None:
    pending = s.info.pop(_PENDING, None)
    if not pending:
        return
    from .services import next_number  # local import: services imports this module

    for ev in pending:  # one counter row per tenant; taken last, held until commit
        ev.recorded_seq = next_number(s, ev.tenant_id, "event", OutboxEvent, OutboxEvent.recorded_seq)
        s.add(ev)
    s.flush()


@sa_event.listens_for(Session, "after_soft_rollback")
def _drop_pending_events(s: Session, previous_transaction) -> None:
    if not previous_transaction.nested:  # a savepoint rollback keeps the outer events
        s.info.pop(_PENDING, None)


# ------------------------------------------------------------------ reading


def latest_seq(s: Session, tenant_id: str) -> int:
    return s.scalar(select(func.coalesce(func.max(OutboxEvent.recorded_seq), 0))
                    .where(OutboxEvent.tenant_id == tenant_id)) or 0


def events_after(s: Session, tenant_id: str, after: int, limit: int = 200,
                 location_id: int | None = None) -> list[OutboxEvent]:
    q = (select(OutboxEvent).where(OutboxEvent.tenant_id == tenant_id, OutboxEvent.recorded_seq > after)
         .order_by(OutboxEvent.recorded_seq).limit(limit))
    rows = list(s.scalars(q))
    if location_id is not None:  # tenant-wide events (location NULL) are visible everywhere
        rows = [e for e in rows if e.location_id in (None, location_id)]
    return rows


def invalidation(ev: OutboxEvent) -> dict:
    """What the realtime feed sends: identifiers only, never payload data.
    Clients re-read through the normal, permission-checked API."""
    return {"seq": ev.recorded_seq, "type": ev.type, "aggregate_type": ev.aggregate_type,
            "aggregate_id": ev.aggregate_id, "aggregate_version": ev.aggregate_version,
            "location_id": ev.location_id}


# ------------------------------------------------------------------ consumers

Handler = Callable[[Session, OutboxEvent], None]
CONSUMERS: dict[str, Handler] = {}


def consumer(name: str):
    def register(fn: Handler) -> Handler:
        CONSUMERS[name] = fn
        return fn
    return register


@dataclass
class RunResult:
    processed: int = 0
    skipped_duplicates: int = 0
    dead_lettered: int = 0
    failed: bool = False
    lease_lost: bool = False
    not_leased: bool = False


def _backoff(attempts: int) -> timedelta:
    base = min(BACKOFF_BASE * (2 ** (attempts - 1)), BACKOFF_MAX)
    return base * (0.5 + random.random() / 2)  # jitter: 50-100 %


def _acquire(s: Session, name: str, tenant_id: str, worker: str, now: datetime) -> EventConsumer | None:
    if s.get(EventConsumer, (name, tenant_id)) is None:
        s.add(EventConsumer(name=name, tenant_id=tenant_id, cursor=0, fencing_token=0, attempts=0, updated_at=now))
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
    res = s.execute(
        update(EventConsumer)
        .where(EventConsumer.name == name, EventConsumer.tenant_id == tenant_id,
               (EventConsumer.lease_until.is_(None)) | (EventConsumer.lease_until < now)
               | (EventConsumer.lease_owner == worker))
        .values(lease_owner=worker, lease_until=now + LEASE, fencing_token=EventConsumer.fencing_token + 1,
                updated_at=now)
        .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        s.rollback()
        return None
    s.commit()
    return s.scalar(select(EventConsumer).where(EventConsumer.name == name, EventConsumer.tenant_id == tenant_id)
                    .execution_options(populate_existing=True))


def _advance(s: Session, c: EventConsumer, token: int, seq: int, now: datetime, **extra) -> bool:
    res = s.execute(update(EventConsumer)
                    .where(EventConsumer.name == c.name, EventConsumer.tenant_id == c.tenant_id,
                           EventConsumer.fencing_token == token)
                    .values(cursor=seq, updated_at=now, **extra).execution_options(synchronize_session=False))
    return res.rowcount == 1


def run_consumer(factory, name: str, tenant_id: str, *, worker: str, now: datetime,
                 batch: int = 100, handler: Handler | None = None) -> RunResult:
    """Process up to ``batch`` events for (consumer, tenant). Safe to run
    from several workers at once: only the lease holder makes progress."""
    from .db import bind_tenant

    fn = handler or CONSUMERS[name]
    out = RunResult()
    with factory() as s:
        bind_tenant(s, tenant_id)
        c = _acquire(s, name, tenant_id, worker, now)
        if c is None:
            out.not_leased = True
            return out
        if c.next_attempt_at is not None and now < c.next_attempt_at:
            return out
        token = c.fencing_token
        evs = events_after(s, tenant_id, c.cursor, batch)
        s.commit()  # read-only; objects stay loaded (expire_on_commit=False)
        for ev in evs:
            try:
                with s.begin_nested():
                    s.add(InboxEntry(consumer=name, event_id=ev.id, tenant_id=tenant_id,
                                     recorded_seq=ev.recorded_seq, processed_at=now))
                    s.flush()
            except IntegrityError:  # already applied (redelivery): just move on
                if not _advance(s, c, token, ev.recorded_seq, now, failing_seq=None, attempts=0,
                                next_attempt_at=None, last_error=None):
                    s.rollback()
                    out.lease_lost = True
                    return out
                s.commit()
                out.skipped_duplicates += 1
                continue
            try:
                fn(s, ev)
                s.flush()
            except Exception as e:  # the handler's effect is rolled back with the inbox row
                s.rollback()
                attempts = (c.attempts if c.failing_seq == ev.recorded_seq else 0) + 1
                err = f"{type(e).__name__}: {e}"[:500]
                if attempts >= MAX_ATTEMPTS:
                    s.add(DeadLetter(consumer=name, tenant_id=tenant_id, event_id=ev.id, recorded_seq=ev.recorded_seq,
                                     attempts=attempts, error=err, created_at=now))
                    ok = _advance(s, c, token, ev.recorded_seq, now, failing_seq=None, attempts=0,
                                  next_attempt_at=None, last_error=err)
                    if not ok:
                        s.rollback()
                        out.lease_lost = True
                        return out
                    s.commit()
                    c.attempts, c.failing_seq = 0, None
                    out.dead_lettered += 1
                    continue
                s.execute(update(EventConsumer)
                          .where(EventConsumer.name == name, EventConsumer.tenant_id == tenant_id,
                                 EventConsumer.fencing_token == token)
                          .values(failing_seq=ev.recorded_seq, attempts=attempts, last_error=err,
                                  next_attempt_at=now + _backoff(attempts), updated_at=now))
                s.commit()
                out.failed = True
                return out
            if not _advance(s, c, token, ev.recorded_seq, now, failing_seq=None, attempts=0,
                            next_attempt_at=None, last_error=None):
                s.rollback()  # lease was taken over: our effect must not count
                out.lease_lost = True
                return out
            s.commit()
            c.attempts, c.failing_seq = 0, None
            out.processed += 1
        s.execute(update(EventConsumer).where(EventConsumer.name == name, EventConsumer.tenant_id == tenant_id,
                                              EventConsumer.fencing_token == token)
                  .values(lease_owner=None, lease_until=None))
        s.commit()
    return out


def replay_dead_letter(factory, dead_letter_id: int, *, tenant_id: str, now: datetime,
                       handler: Handler | None = None) -> None:
    """Operator command: re-apply one dead-lettered event (idempotent via inbox)."""
    from .db import bind_tenant

    with factory() as s:
        bind_tenant(s, tenant_id)
        dl = s.scalar(select(DeadLetter).where(DeadLetter.id == dead_letter_id, DeadLetter.tenant_id == tenant_id))
        if dl is None or dl.resolved_at is not None:
            raise LookupError("dead letter not found or already resolved")
        ev = s.get(OutboxEvent, dl.event_id)
        fn = handler or CONSUMERS[dl.consumer]
        try:
            s.add(InboxEntry(consumer=dl.consumer, event_id=ev.id, tenant_id=tenant_id,
                             recorded_seq=ev.recorded_seq, processed_at=now))
            s.flush()
        except IntegrityError:
            s.rollback()
            dl = s.get(DeadLetter, dead_letter_id)
            dl.resolved_at, dl.resolution = now, "already_applied"
            s.commit()
            return
        fn(s, ev)
        dl.resolved_at, dl.resolution = now, "replayed"
        s.commit()


def consumer_health(s: Session, tenant_id: str) -> list[dict]:
    head = latest_seq(s, tenant_id)
    out = []
    for c in s.scalars(select(EventConsumer).where(EventConsumer.tenant_id == tenant_id)):
        dead = s.scalar(select(func.count()).select_from(DeadLetter).where(
            DeadLetter.consumer == c.name, DeadLetter.tenant_id == tenant_id, DeadLetter.resolved_at.is_(None)))
        out.append({"consumer": c.name, "cursor": c.cursor, "head": head, "lag": head - c.cursor,
                    "attempts": c.attempts, "last_error": c.last_error, "open_dead_letters": dead,
                    "lease_owner": c.lease_owner})
    return out
