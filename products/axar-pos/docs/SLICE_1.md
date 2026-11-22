# Slice 1 — simulated cash sale, receipt, drawer reconciliation

Status: **local demo, not production**. Payments are simulated cash only. No
card terminal, bank, accounting system or secret is touched.

## What was available in GitHub at the start

| Item | State |
| --- | --- |
| `products/axar-pos/README.md`, `docs/ARCHITECTURE.md`, `docs/BUILD_HANDOFF.md` | present (PR #6) |
| Sprint 0 Python source (`axar-pos-sprint0.zip`, "18 tests") | **not in GitHub** — never seen or run by this slice |
| CI for Python | none |

This slice was therefore written from the architecture docs, not ported from
the ZIP. The "18 tests passed" claim in PR #6 is not verified here; this slice
has its own suite.

## Scope (in)

- Catalog read (`GET /api/v1/products`), server-owned VAT-inclusive prices.
- Cash sale `POST /api/v1/sales/cash`:
  - integer cents everywhere; floats/strings rejected with 422; client cannot
    send prices or totals (`extra="forbid"`);
  - per-line VAT from **versioned** rates chosen by business date
    (EE standard 22 % → 24 % on 2025-07-01; reduced 9 %);
  - Estonian 5-cent cash rounding recorded as its own field
    (`cash_rounding_cents`), never folded into VAT/net;
  - `Idempotency-Key` header required; same key + same body replays the
    original sale (200, `Idempotent-Replayed: true`); same key + different
    body is 409; unique `(tenant_id, idempotency_key)` index makes it hold
    under concurrent retries;
  - per-tenant sequential receipt numbers (unique index);
  - immutable line snapshots (name, price, rate) — later price changes do not
    alter past sales;
  - `pos.sale.completed` v1 outbox event written in the **same transaction**
    with tenant, source, version, timestamp, correlation ID and SHA-256
    payload hash (not yet published anywhere).
- Receipts: JSON and plain-text Estonian receipt marked *SIMULEERITUD*.
- Cash drawer: open with float, one open drawer per tenant (DB partial unique
  index), close with counted cash → `expected = float + Σ cash_due`,
  `variance = counted − expected`.
- Daily report for a **Europe/Tallinn business day** with configurable cutoff
  hour, VAT breakdown by rate, rounding total, receipt range.
- Append-only guard: ORM refuses UPDATE/DELETE on sales, lines, outbox.
- Estonian touchscreen UI at `/` (responsive, no external assets, renders
  server data with `textContent` only).

## Scope (out — tracked in issues)

- Real authentication, roles, PostgreSQL, Alembic → #2
- Refunds/voids as compensating records, crash-safe idempotency across
  process restarts with in-flight state, reconciliation vs settlements → #3
- Merit Aktiva / bank adapters, outbox publisher → #4
- Agent orchestration → #5
- Card payments, fiscal compliance, offline mode.

## Tenant-boundary plan

Slice 1 (now):
1. Every business table has `tenant_id`; every service function takes
   `tenant_id` explicitly and every query filters on it.
2. Lookups by ID include `tenant_id` in the WHERE clause, so another tenant's
   record returns **404**, indistinguishable from non-existent (no
   enumeration oracle).
3. Uniqueness (idempotency key, receipt number, SKU, location/register code)
   is per tenant; the open-drawer rule is per register. A register id from
   another tenant behaves as non-existent (404) for open, sell and read.
4. Identity comes from `X-Tenant-ID` / `X-Cashier-ID` **only** when
   `AXAR_POS_DEMO_IDENTITY=1`. Default is off → tenant APIs answer 503. There
   is no implicit demo identity.
5. Tests: `tests/test_tenancy_and_identity.py` (cross-tenant product sale,
   sale/receipt read, listing, report, drawer close, key collision).

Next (#2):
1. Replace header identity with signed tokens (OIDC); tenant and role come
   from verified claims only.
2. PostgreSQL Row-Level Security keyed on a per-transaction `app.tenant_id`
   setting as a second wall behind the query filters.
3. Repository layer that cannot be constructed without a tenant context; a
   lint/test that fails on any `select()` of a tenant table without a
   tenant predicate.
4. Role checks: cashier (sell, open drawer), manager (close drawer with
   variance, reports), owner (catalog).

## Review round 1 (P1 items from AION on PR #7)

### Tenant → location → register → drawer session

`locations` and `registers` tables (tenant-scoped). Drawer sessions belong to a
register; the DB enforces **one open drawer per register** (partial unique
index), so several registers at one location run independent drawers and
reconciliations. Sales record `register_id` and `drawer_session_id`; the
request hash includes `register_id`, so an idempotency key cannot be replayed
onto another register. Daily report has a `by_register` breakdown; receipts
show location and register.

### Sale vs close race — what is fixed and how it is proven

Before: `close_drawer` summed sales, then flipped the status in a separate
step; `create_cash_sale` read "drawer open" and later inserted. A sale could
commit into a drawer that had just been closed, and the closed `expected`
could miss it.

Now, two layers:

1. **Conditional claim (DB-agnostic, the real guard).** Inside the sale
   transaction: `UPDATE drawer_sessions SET cash_sales_cents += due,
   sale_count += 1 WHERE id=? AND tenant_id=? AND status='open'`. Zero rows →
   rollback, 409 `no_open_drawer`, nothing written. Close is one
   `UPDATE … SET status='closed', expected = float + cash_sales_cents …
   WHERE status='open'`, so expected is computed atomically with the status
   flip. After closing, the running totals are compared with the immutable
   ledger (`Σ sales.cash_due`, count); a mismatch rolls back with 500
   `ledger_mismatch` and the drawer stays open for a human.
2. **SQLite write serialization.** Every SQLite transaction starts with
   `BEGIN IMMEDIATE` (pysqlite's own deferred BEGIN disabled), so writers
   cannot interleave at all on the local store.

Evidence:

- `test_stale_open_drawer_read_cannot_commit_into_closed_drawer` forces the
  exact bad interleaving (sale reads "open" → close commits → sale claims).
  **Mutation check:** with the claim guard replaced by `return True`, this
  test fails; with the guard it passes.
- `test_parallel_sales_vs_close_keep_drawer_consistent` (5 variants): 12
  parallel sales + 1 close; asserts every accepted sale is in the closed
  drawer's totals, rejected ones are 409 and absent from the ledger, and no
  ledger sale is timestamped after the close. Note: on SQLite this test also
  passes *without* the claim guard, because `BEGIN IMMEDIATE` already
  serializes — it proves end-to-end consistency, not the guard itself.
- `test_close_refuses_when_running_total_disagrees_with_ledger`.

**Remaining risk (PostgreSQL, not yet tested):** the claim relies on
PostgreSQL row locking plus READ COMMITTED re-evaluation of the `WHERE` after a
concurrent update commits. That is documented PostgreSQL behaviour, but it is
**not exercised by CI** until #2 adds a PostgreSQL service container. #2 must
run the concurrency tests against PostgreSQL before any multi-process
deployment.

### Timezone fix

SQLite drops `tzinfo`; values read back were naive and `astimezone()` then
used the **host's** zone (wrong receipt time on any non-UTC server). New
`UTCDateTime` column type stores UTC and always returns aware UTC, and it
rejects naive input. Test renders a receipt with `TZ=America/New_York` and
still gets Tallinn time.

## Known limitations

- Receipt numbering is per tenant via `max+1` guarded by a unique index
  (serialized on SQLite). Use a per-tenant (or per-register, if fiscal rules
  require) sequence row on PostgreSQL.
- SQLite `BEGIN IMMEDIATE` also serializes read requests; fine for a local
  single-site demo, not a production pattern.
- Schema is created with `create_all`; no migrations yet. **Delete an old
  local `axar_pos.db`** created before review round 1 (schema changed).
- VAT rates and the 5-cent cash rounding rule must be confirmed by an
  accountant before any real use.

## Rollback

All changes are additive and isolated:

- New files only under `products/axar-pos/` and `.github/workflows/axar-pos.yml`.
- No change to the Next.js app, `package.json`, Vercel config or `main`.
- Nothing is deployed; the service runs only on `127.0.0.1` when started
  manually.

To roll back: close the PR / revert the merge commit, or delete
`products/axar-pos/axar_pos`, `products/axar-pos/tests` and the workflow.
Local data lives in a single SQLite file (`axar_pos.db`, git-ignored); delete
it to reset.
