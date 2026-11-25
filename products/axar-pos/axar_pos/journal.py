"""Device journal and per-register receipt series (ADR-0012 stage O1a, #29).

Every money operation appends one entry, in the SAME transaction, to the
journal stream of its register:

    seq        strictly increasing, gap-free per stream (locked counter row)
    summary    sha256 of a canonical summary of the business record
    hash       sha256(prev_hash + canonical(entry))  -> a tamper-evident chain

``verify`` recomputes everything from the database: a missing or reordered
entry, an edited hash, or a later change to the sale/refund itself is
reported. This is tamper EVIDENCE (detection), not prevention; prevention is
the append-only guard + PostgreSQL grants.

Nothing is synced yet (that is O1b); the same journal will be written by the
offline edge node with ``stream_id = device:<id>``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .models import DeviceJournalEntry, Refund, Register, Sale, TenantCounter
from .services import DomainError


class JournalBroken(DomainError):
    """The stream's chain is broken (entry missing). Money operations on this
    register stop until a person investigates - never extend it silently."""

    status_code = 500
    code = "journal_broken"

GENESIS = "0" * 64
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(now_ms: int | None = None) -> str:
    """26-char ULID: 48-bit millisecond time + 80 random bits (sortable)."""
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    value = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def stream_for_register(register_id: int) -> str:
    return f"register:{register_id}"


# ------------------------------------------------------------------ summaries


def sale_summary(sale: Sale, lines) -> dict:
    return {"type": "sale", "id": sale.id, "receipt_no": sale.receipt_no,
            "register_receipt_no": sale.register_receipt_no, "receipt_label": sale.receipt_label,
            "register_id": sale.register_id, "total_cents": sale.total_cents, "vat_cents": sale.vat_cents,
            "cash_due_cents": sale.cash_due_cents, "payment_method": sale.payment_method,
            "lines": sorted([ln.line_no, ln.sku, ln.quantity, ln.gross_cents, ln.vat_rate_bp] for ln in lines)}


def refund_summary(refund: Refund, lines) -> dict:
    return {"type": "refund", "id": refund.id, "refund_no": refund.refund_no,
            "original_sale_id": refund.original_sale_id, "register_id": refund.register_id,
            "total_cents": refund.total_cents, "vat_cents": refund.vat_cents,
            "cash_paid_cents": refund.cash_paid_cents,
            "lines": sorted([ln.line_no, ln.sku, ln.quantity, ln.gross_cents, ln.vat_cents] for ln in lines)}


def _entry_body(e: DeviceJournalEntry) -> dict:
    return {"id": e.id, "stream": e.stream_id, "seq": e.seq, "type": e.entry_type, "entity": e.entity_id,
            "summary": e.summary_hash, "at": e.created_at.isoformat()}


# -------------------------------------------------------------------- writing


def _counter(s: Session, tenant_id: str, name: str, seed, floor: int = 1) -> int:
    """Next value of a per-tenant named counter, never below ``floor``. The
    upsert holds the row lock until commit (gap-free; rollback returns it)."""
    from sqlalchemy.dialects import postgresql, sqlite

    ins = (postgresql.insert if s.get_bind().dialect.name == "postgresql" else sqlite.insert)(TenantCounter)
    nxt = TenantCounter.value + 1
    stmt = ins.values(tenant_id=tenant_id, name=name, value=seed).on_conflict_do_update(
        index_elements=[TenantCounter.tenant_id, TenantCounter.name],
        set_={"value": case((nxt >= floor, nxt), else_=floor)},
    ).returning(TenantCounter.value)
    return s.execute(stmt).scalar_one()


def next_register_receipt(s: Session, tenant_id: str, register: Register) -> tuple[int, str | None]:
    """(number, label) from the register's own series. Lock order: drawer,
    tenant receipt counter, register counter, journal counter."""
    floor = register.receipt_floor or 1
    seed = select(func.max(func.coalesce(func.max(Sale.register_receipt_no), 0) + 1, floor)) \
        .where(Sale.tenant_id == tenant_id, Sale.register_id == register.id).scalar_subquery() \
        if s.get_bind().dialect.name == "sqlite" else \
        select(func.greatest(func.coalesce(func.max(Sale.register_receipt_no), 0) + 1, floor)) \
        .where(Sale.tenant_id == tenant_id, Sale.register_id == register.id).scalar_subquery()
    n = _counter(s, tenant_id, f"rcpt:{register.id}", seed, floor)
    label = f"{register.receipt_prefix}-{n:06d}" if register.receipt_prefix else None
    return n, label


def append(s: Session, *, tenant_id: str, stream_id: str, entry_type: str, entity_id: str,
           summary: dict, now: datetime) -> DeviceJournalEntry:
    seed = select(func.coalesce(func.max(DeviceJournalEntry.seq), 0) + 1).where(
        DeviceJournalEntry.tenant_id == tenant_id, DeviceJournalEntry.stream_id == stream_id).scalar_subquery()
    seq = _counter(s, tenant_id, f"jrnl:{stream_id}"[:32], seed)
    prev = GENESIS if seq == 1 else s.scalar(select(DeviceJournalEntry.hash).where(
        DeviceJournalEntry.tenant_id == tenant_id, DeviceJournalEntry.stream_id == stream_id,
        DeviceJournalEntry.seq == seq - 1))
    if prev is None:
        raise JournalBroken(f"journal {stream_id}: entry {seq - 1} missing; refusing to extend a broken chain")
    e = DeviceJournalEntry(id=ulid(int(now.timestamp() * 1000)), tenant_id=tenant_id, stream_id=stream_id,
                           seq=seq, entry_type=entry_type, entity_id=entity_id,
                           summary_hash=_sha(_canonical(summary)), prev_hash=prev, created_at=now, hash="")
    e.hash = _sha(prev + _canonical(_entry_body(e)))
    s.add(e)
    return e


# ------------------------------------------------------------------ verifying


def verify(s: Session, tenant_id: str, stream_id: str) -> dict:
    """Recompute the chain and every summary from the current records."""
    entries = list(s.scalars(select(DeviceJournalEntry).where(
        DeviceJournalEntry.tenant_id == tenant_id, DeviceJournalEntry.stream_id == stream_id)
        .order_by(DeviceJournalEntry.seq)))
    problems: list[str] = []
    prev = GENESIS
    for i, e in enumerate(entries, start=1):
        if e.seq != i:
            problems.append(f"seq {i} missing (found {e.seq})")
            break
        if e.prev_hash != prev:
            problems.append(f"seq {e.seq}: prev_hash does not link to seq {e.seq - 1}")
        if _sha(e.prev_hash + _canonical(_entry_body(e))) != e.hash:
            problems.append(f"seq {e.seq}: entry hash does not match its content")
        current = _current_summary(s, tenant_id, e)
        if current is None:
            problems.append(f"seq {e.seq}: {e.entry_type} {e.entity_id} no longer exists")
        elif _sha(_canonical(current)) != e.summary_hash:
            problems.append(f"seq {e.seq}: {e.entry_type} {e.entity_id} was changed after it was journaled")
        prev = e.hash
    return {"stream_id": stream_id, "entries": len(entries), "head_hash": prev, "ok": not problems,
            "problems": problems}


def _current_summary(s: Session, tenant_id: str, e: DeviceJournalEntry) -> dict | None:
    if e.entry_type == "sale":
        sale = s.scalar(select(Sale).where(Sale.id == e.entity_id, Sale.tenant_id == tenant_id))
        return None if sale is None else sale_summary(sale, sale.lines)
    if e.entry_type == "refund":
        rf = s.scalar(select(Refund).where(Refund.id == e.entity_id, Refund.tenant_id == tenant_id))
        return None if rf is None else refund_summary(rf, rf.lines)
    return None
