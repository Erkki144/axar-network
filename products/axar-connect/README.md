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

## Current Claude POS contract adapter (v1)

`from_pos_outbox_v1(...)` parses the exact immutable `pos.sale.completed` v1
payload in Claude's PR #7, verifies the SHA-256 checksum and validates sale
amounts. It constructs a journal-preview input, without any network or posting.
This adapter must run *only* on authenticated trusted internal events: a hash is
an integrity check, **not** a digital signature or authorization mechanism.


## Refunding (PR #7 Slice 2) — new v0.4 adapter

`from_pos_refund_outbox_v1(...)` accepts only the exact trusted, checksum-verified
`pos.refund.completed` v1 *simulated cash* event, rejecting malformed money,
negative net line allocations, wrong event versions, unsupported payment types
and inaccurate line totals. `preview_cash_refund_journal(...)` produces a
**balanced, non-posting accounting reversal preview**. It does not issue a
payment or contact an accounting provider.

**Important issue for Claude:** partial-return VAT cannot always be computed
independently per one-unit refund. For a sale of 100 × €0.01, original VAT is
€0.19 at 24%; if 99 units are refunded one at a time with zero VAT, allocating
remaining €0.19 VAT to the final €0.01 refund produces **negative €0.18 net**.
Our adapter rejects this, but the POS refund allocation itself needs a fix.
Cash rounding across many partial refunds also needs cumulative allocation to
ensure a fully reversed sale repays exactly the originally collected amount.

**Not yet proven:** cumulative per-sale refunded VAT/quantity/cash caps across
all separate events, ledger mapping approval, verified transport identity,
PostgreSQL concurrency, external bank/payment/accounting integration.
