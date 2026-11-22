# RFC-0003 — `pos.refund.completed` v2 (proposal)

Status: **DRAFT.** v1 stays unchanged and keeps being emitted. Nothing here is
implemented yet.

## Why

Refund VAT is allocated **cumulatively pro-rata** per original sale line
(`docs/SLICE_2_REFUNDS.md`), not as `vat_from_gross(refund_line_gross)`.
A consumer such as AXAR CONNECT (#8) therefore cannot validate a refund line on
its own. It needs a stable link to the original line and the cumulative state.
v1 carries `original_sale_id` but no per-line reference.

## v2 payload additions (per refund line)

| Field | Meaning |
| --- | --- |
| `sale_line_id` | stable id of the original sale line |
| `original_line_no` | line number on the original receipt |
| `original_quantity`, `original_gross_cents`, `original_vat_cents`, `vat_rate_bp` | immutable original line values |
| `refunded_quantity_after`, `refunded_gross_after_cents`, `refunded_vat_after_cents` | cumulative totals for that line **including** this refund |

Per refund (top level), in addition to v1:
- `tenant_id` and `source` inside the payload. They must equal the envelope;
  consumers reject a mismatch.
- `original_sale_event_id`: the `pos.sale.completed` event of the sale.
- `sale_refunded_gross_after_cents` and `sale_refunded_cash_after_cents`: the
  cumulative per-sale cash state.

## Consumer checks that v2 enables

- Per line: `0 ≤ vat ≤ gross`, and `refunded_vat_after = half_up(original_vat ×
  refunded_quantity_after / original_quantity)`.
- Per sale: `sale_refunded_cash_after = round_cash(sale_refunded_gross_after)`.
  When everything is refunded, the per-sale cash equals the original `cash_due`.
- The previous cumulative value + this refund = the new cumulative value. This
  detects gaps (a missed refund event).

## Versioning and rollout

1. The POS emits **both** v1 and v2 for each refund (two outbox rows, same
   correlation id) for a transition period.
2. Consumers dispatch on `(type, version)` explicitly and reject unknown
   versions. v1 is not changed.
3. v1 is retired only after all consumers confirm v2 and the owner approves.

## Delivery semantics and required tests

Delivery is at-least-once, unordered across event types.
- **Duplicate delivery:** the same event id twice produces one effect. Consumers
  dedupe on `(tenant_id, event_id)`.
- **Replay:** reprocessing the whole outbox from the start gives identical
  results (deterministic ids, as CONNECT already does for journals).
- **Out of order:** a refund event arriving before its sale event is parked,
  not rejected and not applied, until the sale event is seen. Refunds of the
  same line arriving out of order are applied in `refunded_quantity_after`
  order. A gap stays pending and alerts.
- **Cross-tenant:** a payload `tenant_id` that differs from the envelope is
  rejected.

POS-side tests (to add with the implementation): v2 payload fields equal the DB
state after each refund; the v1 payload stays byte-identical to today's format;
both events share the correlation id.
