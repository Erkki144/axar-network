# 04 — Event catalog

Delivery semantics (ADR-0004):
- **at-least-once** with idempotent consumers;
- ordering only per aggregate (`aggregate_version`);
- never "exactly once".

Producers write events in the **same transaction** as the state change. A consumer records `(consumer, event_id)` in its inbox in the same transaction as its effect.

## Emitted today (v1, schema-checked by `tests/test_event_contracts.py`)

| Type | v | Producer (code) | Schema | Consumers |
| --- | --- | --- | --- | --- |
| `pos.sale.completed` | 1 | `services._sale_completed_event` | `contracts/events/pos.sale.completed.v1.schema.json` | CONNECT `from_pos_outbox_v1` (PR #8): 44/44 real events accepted |
| `pos.refund.completed` | 1 | `services._refund_completed_event` | `contracts/events/pos.refund.completed.v1.schema.json` | CONNECT `from_pos_refund_outbox_v1`: 20/20 accepted |

## Planned (envelope v2, `contracts/events/envelope.v2.schema.json`)

| Type | Aggregate | When | Consumers |
| --- | --- | --- | --- |
| `pos.sale.completed` v2 | sale | same as v1 + currency, simulated, actor | BOOKS, STOCK, reports |
| `pos.refund.completed` v2 | refund | + stock disposition per line | BOOKS, STOCK |
| `drawer.opened` / `drawer.closed` | drawer_session | open/close commit; close carries expected/counted/variance and the approval ID | BOOKS (cash over/short), RECON |
| `order.opened` / `order.lines_changed` / `order.closed` | order | every version bump | KDS read model, SSE, GUEST |
| `kitchen.ticket.created` / `.status_changed` | kitchen_ticket | send / accept / preparing / ready / served / cancelled | KDS screens, NOTIFY, GUEST status |
| `payment.intent.created` / `.succeeded` / `.failed` / `.refunded` | payment_intent | PAY state transitions **after webhook verification** | ORDERS (release to kitchen), BOOKS |
| `stock.movement.posted` | stock_movement | receipt / consumption / production / waste / count / transfer | COGS, BOOKS, RECON |
| `notify.requested` / `.attempted` / `.failed` | notification | outbox-driven; "attempted" ≠ delivered | staff exception queue |
| `recon.mismatch.detected` | reconciliation_run | daily or on close | exception queue, alert |

## Versioning rules

1. A payload schema is closed (`additionalProperties: false`), so adding a field is a new version.
2. A producer emits the old and new version in parallel until every registered consumer acknowledges migrating (tracked in the consumer registry).
3. A consumer rejects unknown versions to the dead-letter queue with a reason, and never guesses.
4. Breaking semantic changes (meaning of an amount) need an ADR.
