# AXAR POS architecture — ADR-0001

## Scope

AXAR ONE is an independent commercial platform. POS is its first bounded context. The current AXAR Network marketing application is out of scope.

## Modules

- **POS:** catalog, cart, immutable sale snapshots, cash drawer, refunds.
- **PAY:** official terminal integrations, provider-confirmed states and settlements.
- **BANK:** consented read-only bank feeds first, payment initiation only with lawful authorization and explicit approval.
- **BOOKS:** bookkeeping adapter interface, preview/posted batch states, duplicate prevention.
- **STOCK:** recipe costing and inventory events.
- **BUILD:** AI-assisted coding orchestration, completely separate from POS runtime.

## Financial invariants

1. Server calculates money in integer cents or Decimal, never client float totals.
2. Each payment/sale has unique idempotency keys and event correlation IDs.
3. Card transaction is not paid until provider success is verified.
4. Original paid records must be traceable; refunds and corrections create additional auditable events.
5. Every access must be tenant-scoped and permission-checked before SaaS deployment.
6. Accounting exports are idempotent and reconcile to payment settlements and bank postings.
7. LLM output cannot directly execute real transfers or tax filings.

## Infrastructure direction

FastAPI, PostgreSQL, Alembic migrations, web POS UI, local SQLite store for offline resilience, transactional outbox, structured audit log, Docker and GitHub Actions.

The initial local cash-demo uses SQLite and a simple standalone browser UI to minimize dependencies. It is **not** a licensed payment system, compliant fiscal cash register, secure multi-tenant system or fully offline-capable POS.

## Integration contracts

`pos.sale.completed.v1`, `pay.payment.confirmed.v1`, `pay.settlement.received.v1`, `bank.transaction.received.v1`, `books.batch.prepared.v1`, `recon.mismatch.detected.v1`.

Each event will include a unique ID, tenant, source, version, timestamp, correlation ID and payload hash.
