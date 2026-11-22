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
3. Uniqueness (idempotency key, receipt number, SKU, open drawer) is per
   tenant.
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

## Known limitations

- Drawer close vs in-flight sale is not serialized: a sale that commits
  between "compute expected" and "close" could be attributed to a closed
  drawer. Fix in #3 with `SELECT … FOR UPDATE` on PostgreSQL.
- Receipt numbering uses `max+1` guarded by a unique index; a collision
  returns 409 and the client retries with the same key. Use a per-tenant
  sequence row on PostgreSQL.
- Schema is created with `create_all`; no migrations yet.
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
