"""Registered event consumers. Handlers run INSIDE the consumer transaction
(``events.run_consumer``): they may only write to the database. Anything
external goes through a dedicated outbox (``export_queue``) delivered
separately, so a crash can never leave a half-applied external effect."""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from .events import consumer
from .models import ExportQueue, OutboxEvent

BOOKS_EVENTS = {("pos.sale.completed", 1), ("pos.refund.completed", 1)}


class CorruptEvent(ValueError):
    pass


@consumer("books.export", on_poison="block")  # financial: never out of order
def books_export(s: Session, ev: OutboxEvent) -> None:
    if (ev.type, ev.version) not in BOOKS_EVENTS:
        return  # not relevant for accounting
    if hashlib.sha256(ev.payload.encode()).hexdigest() != ev.payload_hash:
        raise CorruptEvent("payload hash mismatch")  # quarantined by the block policy
    s.add(ExportQueue(tenant_id=ev.tenant_id, destination="books", event_id=ev.id, event_type=ev.type,
                      event_version=ev.version, recorded_seq=ev.recorded_seq, payload=ev.payload,
                      payload_hash=ev.payload_hash, status="pending", created_at=ev.occurred_at))


@consumer("stock.consume", on_poison="block")  # inventory: never out of order
def stock_consume(s: Session, ev: OutboxEvent) -> None:
    """A completed sale consumes stock (recipe ingredients or a stocked item).
    Idempotent twice over: the inbox, and a unique movement reference per
    (sale, line, item). Refunds do not restock (explicit disposition later)."""
    if (ev.type, ev.version) != ("pos.sale.completed", 1):
        return
    import json
    from datetime import date

    from .inventory import consume_for_sale

    p = json.loads(ev.payload)
    consume_for_sale(s, ev.tenant_id, sale_id=p["sale_id"], register_id=p["register_id"],
                     business_date=date.fromisoformat(p["business_date"]),
                     lines=[(ln["sku"], ln["quantity"]) for ln in p["lines"]], now=ev.occurred_at)
