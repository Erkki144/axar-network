# Slice 2 — simulated cash refunds (issue #3, part 1)

Depends on PR #7 (Slice 1 + review round 1). Status: **local demo,
simulated cash only**. No card or bank refund is modelled or claimed.

## Scope (in)

- `POST /api/v1/sales/{sale_id}/refunds` with `Idempotency-Key`:
  `{register_id, lines:[{line_no, quantity}], reason}`. Full or partial,
  one or many lines, any register of the same tenant, any later day.
- A refund is a **separate append-only record** (`refunds`, `refund_lines`)
  that references the original sale and the original sale line. The original
  sale, its lines and its `pos.sale.completed` event are never changed (ORM
  guard + test that the original event hash is unchanged).
- `pos.refund.completed` v1 outbox event in the same transaction, carrying
  `original_sale_id`, `original_receipt_no` and `original_correlation_id`.
- Estonian refund receipt (`/api/v1/refunds/{id}/receipt.txt`) marked
  *TAGASTUS — SIMULEERITUD*, with the original receipt number, approver and
  reason.
- Drawer: `cash_refunds_cents` / `refund_count` running totals;
  `expected = float + cash sales − cash refunds`; close verifies both sales
  and refunds against the ledgers.
- Daily report: refund totals, refund VAT breakdown, net totals
  (sales − refunds of that business day), per-register refunds.
- UI: "Tagasta see müük" after a sale (demo sends `X-Role: manager`).

## Guards and how each is proven

| Rule | Mechanism | Test that fails without it |
| --- | --- | --- |
| Never refund more units than sold | per-line `refund_allocations` row, conditional `UPDATE … WHERE refunded_quantity + q <= original_quantity` | `test_over_refund_rejected_and_nothing_written`, `test_parallel_full_refunds_only_one_wins` (10 parallel → exactly 1) |
| No VAT drift: refunded VAT of a fully refunded line equals the sale's VAT | last units take the exact remainder; **compare-and-swap** `WHERE refunded_quantity = <value read>` forces a re-read if anyone refunded meanwhile | `test_stale_read_cannot_cause_vat_drift_on_last_unit` — **mutation-checked**: removing the CAS makes it fail (refund 48 instead of 49) |
| Cannot pay out cash the drawer does not hold | drawer claim `WHERE status='open' AND float + sales − refunds >= cash_paid` | `test_cannot_pay_out_more_cash_than_drawer_holds` — **mutation-checked** |
| Refund cannot land in a closed drawer | same conditional drawer UPDATE | `test_refund_needs_open_drawer_on_that_register` |
| All-or-nothing | any domain error rolls back every counter already moved | `test_failed_second_line_rolls_back_first_line_counter` |
| Idempotent retries | unique `(tenant_id, idempotency_key)` on refunds, body hash | replay / conflicting-body tests |
| Tenant isolation | every lookup tenant-filtered; 404 across tenants | `test_refunds_are_tenant_scoped` |
| Role | `manager` or `owner` only (`X-Role`, demo identity) | `test_cashier_cannot_refund_and_invalid_role_rejected` |

Cash rounding: the refund total is rounded to 5 cents for the cash paid out,
recorded separately as `cash_rounding_cents`. A full refund of a sale pays out
exactly what the customer paid (e.g. 6,99 € → 7,00 € both ways).

## Honest limits

- **Role is not authenticated.** `X-Role` is a header trusted only in demo
  mode. Real role enforcement (and the approver being a different, verified
  person from the cashier) depends on #2. `approved_by` is currently the
  caller's own cashier id.
- PostgreSQL behaviour of the conditional UPDATEs (row lock + READ COMMITTED
  re-check) is not exercised in CI; same follow-up as Slice 1 (#2).
- No void-before-payment, exchanges, card/bank refunds, refund of rounding
  only, or receipt reprint audit.
- VAT on a refund uses the **original line's rate** (not the rate on the
  refund date). Confirm with an accountant.
- New tables (`refund_allocations`, `refunds`, `refund_lines`) and drawer
  columns: delete any old local `axar_pos.db`.

## Rollback

Additive. Revert the commit(s) of this PR; Slice 1 behaviour is unchanged
except the drawer `expected` formula, which equals Slice 1 when there are no
refunds.
