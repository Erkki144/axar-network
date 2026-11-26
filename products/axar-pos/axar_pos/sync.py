"""Offline-first O1b, CENTRAL side: ingest of device journals (ADR-0012 §7).

A till (edge node) pushes its journal entries in strict ``seq`` order. For
each entry, in ONE transaction:

    lock the device cursor -> check seq / chain / payload hash
      -> apply (create the sale with idempotency key ``edge:<entry id>``)
         or quarantine with a reason
      -> write the inbox row + advance the cursor -> commit

Guarantees (tested in tests/test_offline_sync.py):
- exactly once: a re-sent entry is ``duplicate`` (same hash) and changes
  nothing; the sale's idempotency key makes even a re-application a no-op;
- order: an entry that does not follow the cursor is a ``gap`` - nothing after
  it in the batch is processed, the device resends from ``acked_through``;
- integrity: a broken hash chain or payload hash stops the stream
  (``chain_broken``); a different entry for an already used seq is a
  ``conflict``. Neither is applied; both are audited;
- nothing is dropped: an entry that cannot be applied (unknown register or
  product, closed drawer, price differs from what the till charged, refund
  sync not built yet) is acknowledged as ``quarantined`` so the stream moves
  on, and waits for a manager to ``retry`` (same idempotency key) or
  ``dismiss`` it with a note. Money taken at the till is never silently lost.

O1b limitation (documented): a sale is applied at CENTRAL prices; if they
differ from what the till charged, it is quarantined instead of recorded
"as sold". Recording at the snapshot price comes with the signed snapshot (O2).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from . import auth, services
from .journal import GENESIS, wire_hash_ok
from .models import DeviceCursor, DeviceInbox, Product, Register

TERMINAL_STOP = {"gap", "chain_broken", "conflict"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Item:
    entry: dict
    payload: str
    payload_hash: str


def _lock_cursor(s: Session, tenant_id: str, device_id: str, now: datetime) -> DeviceCursor:
    ins = (postgresql.insert if s.get_bind().dialect.name == "postgresql" else sqlite.insert)(DeviceCursor)
    s.execute(ins.values(tenant_id=tenant_id, device_id=device_id, last_seq=0, last_hash=GENESIS,
                         updated_at=now).on_conflict_do_nothing())
    return s.scalar(select(DeviceCursor).where(DeviceCursor.tenant_id == tenant_id,
                                               DeviceCursor.device_id == device_id)
                    .with_for_update().execution_options(populate_existing=True))


def _audit(s: Session, now, tenant_id, device_id, event, detail) -> None:
    auth.audit(s, now, event, "refused", tenant_id=tenant_id, device_id=device_id, detail=json.dumps(detail)[:300])


# ---------------------------------------------------------------- applying


class _Quarantine(Exception):
    pass


def _plan_sale(s: Session, tenant_id: str, payload: dict, settings, now) -> tuple[Register, list, dict]:
    reg = s.scalar(select(Register).where(Register.tenant_id == tenant_id, Register.code == payload.get("register_code"),
                                          Register.active.is_(True)))
    if reg is None:
        raise _Quarantine(f"unknown register {payload.get('register_code')!r}")
    lines = []
    for sku, qty in payload.get("lines", []):
        pid = s.scalar(select(Product.id).where(Product.tenant_id == tenant_id, Product.sku == sku,
                                                Product.active.is_(True)))
        if pid is None:
            raise _Quarantine(f"unknown or inactive product {sku!r}")
        lines.append(services.LineRequest(pid, int(qty)))
    if not lines:
        raise _Quarantine("sale without lines")
    priced = services._price_lines(s, tenant_id, lines, services.business_date_for(now, settings), None,
                                   location_id=reg.location_id, at=now)
    central_total = sum(x.gross_cents for x in priced)
    expected = payload.get("expect", {}).get("total_cents")
    if central_total != expected:
        raise _Quarantine(f"price_mismatch: till charged {expected}, central prices give {central_total}")
    return reg, lines, payload


def _apply(s: Session, *, tenant_id: str, device_id: str, w: dict, payload: dict, settings, now,
           on_success) -> str:
    """Apply one entry; ``on_success(sess, sale_id)`` runs inside the same
    transaction just before commit. Returns the sale id. Raises _Quarantine."""
    if w["type"] == "refund":
        return _apply_refund(s, tenant_id=tenant_id, w=w, payload=payload, settings=settings, now=now,
                             on_success=on_success)
    if w["type"] != "sale":
        raise _Quarantine(f"unknown entry type {w['type']!r}")
    if payload.get("kind") == "bill":
        raise _Quarantine("table bill paid offline (split/card/transfer tenders) - automatic sync is not "
                          "supported yet; review and record manually")
    reg, lines, payload = _plan_sale(s, tenant_id, payload, settings, now)
    try:
        sale, replayed = services.create_cash_sale(
            s, tenant_id=tenant_id, register_id=reg.id, cashier_id=str(payload.get("cashier_id") or "edge")[:64],
            idempotency_key=f"edge:{w['id']}", lines=lines, tendered_cents=int(payload["tendered_cents"]),
            settings=settings, now=now, before_commit=lambda sess, sale: on_success(sess, sale.id),
            hash_extra={"edge_device": device_id})
    except services.DomainError as e:
        s.rollback()
        raise _Quarantine(f"{e.code}: {e}") from None
    if replayed:                       # already applied earlier (e.g. a manager retry): record, don't resell
        on_success(s, sale.id)
        s.commit()
    return sale.id


class _RefundMismatch(services.DomainError):
    code = "price_mismatch"


def _apply_refund(s: Session, *, tenant_id: str, w: dict, payload: dict, settings, now, on_success) -> str:
    """O3: a refund made on the till is applied to the CENTRAL copy of its
    original sale (found through the sale's journal entry), with idempotency
    key ``edge:<refund entry id>``. Anything that does not match exactly goes
    to the review queue. Returns the refund id."""
    orig_entry = payload.get("original_entry_id")
    if not orig_entry:
        raise _Quarantine("refund of a sale that was not made on this till offline; review manually")
    orig = s.scalar(select(DeviceInbox).where(DeviceInbox.tenant_id == tenant_id, DeviceInbox.entry_id == orig_entry))
    if orig is None:
        raise _Quarantine("the original sale has not been received yet")
    if orig.status not in ("accepted", "resolved") or not orig.sale_id:
        raise _Quarantine(f"the original sale is {orig.status} (resolve it first, then retry this refund)")
    if payload.get("role") not in services.REFUND_ROLES:
        raise _Quarantine(f"refund role {payload.get('role')!r} is not allowed to refund")
    reg = s.scalar(select(Register).where(Register.tenant_id == tenant_id, Register.code == payload.get("register_code"),
                                          Register.active.is_(True)))
    if reg is None:
        raise _Quarantine(f"unknown register {payload.get('register_code')!r}")
    lines = [services.RefundLineRequest(int(no), int(qty)) for no, qty in payload.get("sale_lines", [])]
    if not lines:
        raise _Quarantine("refund without lines")
    expected = (payload.get("expect") or {}).get("total_cents")

    def check_then_record(sess: Session, refund) -> None:
        if refund.total_cents != expected:
            raise _RefundMismatch(f"till refunded {expected}, central sale gives {refund.total_cents}")
        on_success(sess, refund.id)

    try:
        refund, replayed = services.create_cash_refund(
            s, tenant_id=tenant_id, register_id=reg.id, cashier_id=str(payload.get("cashier_id") or "edge")[:64],
            role=payload["role"], sale_id=orig.sale_id, idempotency_key=f"edge:{w['id']}", lines=lines,
            reason=str(payload.get("reason") or "offline refund")[:200], settings=settings, now=now,
            approved_by=str(payload.get("approved_by") or "")[:64] or None, before_commit=check_then_record)
    except services.DomainError as e:
        s.rollback()
        raise _Quarantine(f"{e.code}: {e}") from None
    if replayed:                       # applied earlier (e.g. manager retry): record, never refund twice
        on_success(s, refund.id)
        s.commit()
    return refund.id


# ------------------------------------------------------------------ ingest


def ingest(s: Session, *, tenant_id: str, device_id: str, items: list[Item], settings, now: datetime) -> dict:
    results = []
    for it in sorted(items, key=lambda i: int(i.entry.get("seq", 0))):
        r = _ingest_one(s, tenant_id, device_id, it, settings, now)
        results.append(r)
        if r["status"] in TERMINAL_STOP:
            break
    cur = s.scalar(select(DeviceCursor).where(DeviceCursor.tenant_id == tenant_id, DeviceCursor.device_id == device_id))
    s.commit()
    return {"device_id": device_id, "acked_through": cur.last_seq if cur else 0, "results": results}


def _ingest_one(s: Session, tenant_id: str, device_id: str, it: Item, settings, now: datetime) -> dict:
    w = it.entry
    seq = int(w["seq"])
    out = {"seq": seq, "entry_id": w.get("id")}
    cur = _lock_cursor(s, tenant_id, device_id, now)
    last_hash = cur.last_hash
    if seq <= cur.last_seq:
        prior = s.scalar(select(DeviceInbox).where(DeviceInbox.tenant_id == tenant_id,
                                                   DeviceInbox.device_id == device_id, DeviceInbox.seq == seq))
        s.rollback()
        if prior is not None and prior.entry_hash == w.get("hash"):
            return {**out, "status": "duplicate", "server_status": prior.status, "sale_id": prior.sale_id}
        _audit(s, now, tenant_id, device_id, "sync.conflict", {"seq": seq, "entry": w.get("id")})
        s.commit()
        return {**out, "status": "conflict", "reason": "this seq was already received with different content"}
    if seq != cur.last_seq + 1:
        expected = cur.last_seq + 1                         # read before rollback expires the row
        s.rollback()
        return {**out, "status": "gap", "reason": f"expected seq {expected}"}
    problem = None
    if w.get("stream") != f"device:{device_id}":
        problem = "entry belongs to another stream"
    elif w.get("prev_hash") != last_hash:
        problem = "prev_hash does not link to the last received entry"
    elif not wire_hash_ok(w):
        problem = "entry hash does not match its content"
    elif _sha(it.payload) != it.payload_hash:
        problem = "payload hash mismatch"
    if problem:
        s.rollback()
        _audit(s, now, tenant_id, device_id, "sync.chain_broken", {"seq": seq, "problem": problem})
        s.commit()
        return {**out, "status": "chain_broken", "reason": problem}

    def record(sess: Session, status: str, sale_id: str | None, reason: str | None) -> None:
        c = sess.scalar(select(DeviceCursor).where(DeviceCursor.tenant_id == tenant_id,
                                                   DeviceCursor.device_id == device_id))
        c.last_seq, c.last_hash, c.updated_at = seq, w["hash"], now
        sess.add(DeviceInbox(tenant_id=tenant_id, device_id=device_id, seq=seq, entry_id=w["id"],
                             entry_type=w["type"], entry_hash=w["hash"], payload=it.payload,
                             payload_hash=it.payload_hash, status=status, reason=reason, sale_id=sale_id,
                             received_at=now))

    try:
        payload = json.loads(it.payload)
        sale_id = _apply(s, tenant_id=tenant_id, device_id=device_id, w=w, payload=payload, settings=settings,
                         now=now, on_success=lambda sess, sid: record(sess, "accepted", sid, None))
        return {**out, "status": "accepted", "sale_id": sale_id}
    except (_Quarantine, ValueError, KeyError, TypeError) as e:
        s.rollback()
        reason = str(e)[:300] if isinstance(e, _Quarantine) else f"invalid payload: {type(e).__name__}"
        again = _lock_cursor(s, tenant_id, device_id, now)   # re-take the lock after rollback
        if again.last_seq != seq - 1:                       # a parallel push of this device moved on
            s.rollback()
            return {**out, "status": "gap", "reason": f"expected seq {again.last_seq + 1}"}
        record(s, "quarantined", None, reason)
        s.commit()
        return {**out, "status": "quarantined", "reason": reason}


# --------------------------------------------------------------- quarantine


def quarantine(s: Session, tenant_id: str) -> list[DeviceInbox]:
    return list(s.scalars(select(DeviceInbox).where(DeviceInbox.tenant_id == tenant_id,
                                                    DeviceInbox.status == "quarantined")
                          .order_by(DeviceInbox.device_id, DeviceInbox.seq)))


def _locked_row(s: Session, tenant_id: str, row_id: int) -> DeviceInbox:
    row = s.scalar(select(DeviceInbox).where(DeviceInbox.id == row_id, DeviceInbox.tenant_id == tenant_id)
                   .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise services.NotFound("quarantine entry not found")
    if row.status != "quarantined":
        raise services.Conflict(f"entry is already {row.status}")
    return row


def retry(s: Session, *, tenant_id: str, row_id: int, actor: str, settings, now: datetime) -> DeviceInbox:
    """Apply a quarantined entry again (after fixing the cause, e.g. opening
    the drawer or adding the product). Same idempotency key: never sells twice."""
    row = _locked_row(s, tenant_id, row_id)
    w = {"id": row.entry_id, "type": row.entry_type}

    def done(sess: Session, sale_id: str) -> None:
        r = sess.get(DeviceInbox, row.id)
        r.status, r.sale_id, r.resolved_by, r.resolved_at, r.resolution_note = \
            "resolved", sale_id, actor, now, "applied on retry"

    try:
        _apply(s, tenant_id=tenant_id, device_id=row.device_id, w=w, payload=json.loads(row.payload),
               settings=settings, now=now, on_success=done)
    except _Quarantine as e:
        s.rollback()
        raise services.Conflict(f"still cannot be applied: {e}") from None
    return s.get(DeviceInbox, row_id)


def dismiss(s: Session, *, tenant_id: str, row_id: int, actor: str, note: str, now: datetime) -> DeviceInbox:
    if len(note.strip()) < 5:
        raise services.DomainError("a reason (at least 5 characters) is required")
    row = _locked_row(s, tenant_id, row_id)
    row.status, row.resolved_by, row.resolved_at, row.resolution_note = "dismissed", actor, now, note.strip()[:300]
    auth.audit(s, now, "sync.quarantine_dismissed", "ok", tenant_id=tenant_id, staff_id=actor,
               detail=json.dumps({"device": row.device_id, "seq": row.seq, "note": note[:200]}))
    s.commit()
    return row
