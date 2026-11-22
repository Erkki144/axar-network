# AXAR CONNECT — AION's independent financial adapter foundations

An additive experimental module for AXAR POS, isolated from Claude's cash-sale
kernel. **Not production-ready**. Does not invoke any bank or accounting API.

## Features implemented

- Strict integer-cent, tenant-scoped, immutable **cash sale snapshot** contract.
- **Double-entry journal preview** with configurable sales, cash, VAT and
  rounding accounts; original VAT line breakdown remains authoritative.
- **Payout-to-bank reconciliation**: compares gross, fees, refunds and net,
  matches by exact normalized payout reference AND tenant AND currency, refuses
  ambiguous duplicates. Never auto-matches by amount alone.
- **Idempotent SQLite outbox staging**: same tenant + destination + event ID and
  identical document gives one pending job; different body is a conflict.
  A verified provider acknowledgment must be explicitly supplied by a future
  authenticated connector. No automatic external submission is implemented.
- Adversarial regression tests for amounts, tenancy, duplicates, mismatched
  references, multiple VAT rates, replay, persistence and concurrent staging.

## Run tests locally

```bash
cd products/axar-connect
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

## Engineering integration contract

1. Convert the **already committed** `pos.sale.completed.v1` event's sale line
   snapshots to `CashSale`. Validate that tenant, register, receipt and currency
   come from a verified trusted source (not arbitrary HTTP headers).
2. Use `preview_cash_journal(...)` with a verified merchant/accountant's account
   mapping to generate a balanced **preview**, not a posted entry or tax invoice.
3. Stage the result to `SQLiteOutbox` (destination `merit-mock` is illustrative).
4. Before any actual export, implement sandbox adapters with API credentials,
   scoped permissions, verified provider receipts, fail/retry/reconcile policy,
   consent and auditing. The present outbox has **no leasing/multi-worker
   dispatch**, so it MUST NOT be used for a production message worker.
5. Compare provider's payout statement to bank **read-only** entries using
   `reconcile_payouts(...)`; mismatches become exceptions for review.

`account_id` and `VAT account` values are business-specific: never hardcode a
country-wide accounting chart without an accountant approving it. VAT rates
and retail fiscal rules require separate independent legal/accounting review.

**No live banking, no card processing, no real bank transfers, no live Merit
export, no credentials, no customer data and no auto-deploy.**
