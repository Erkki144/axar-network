# ADR-0004 — Transactional outbox v2, relay, inbox; at-least-once

Status: **Proposed** (fixes G-EV-1/2/4). Next implementation slice: C1.

**Decision.**
- **Producers** write envelope v2 (`contracts/events/envelope.v2.schema.json`) in the same transaction as the state change. `recorded_seq` is a per-tenant counter taken from `tenant_counters`, so it is gap-free and ordered by commit within a tenant.
- **Consumers** are registered in `event_consumers(name, tenant, cursor)`. A worker reads `recorded_seq > cursor` with `FOR UPDATE SKIP LOCKED` on a lease row, so two workers cannot process the same consumer. It writes `inbox(consumer, event_id)` plus the effect plus the cursor in one transaction.
- **Failures:** retries with exponential backoff and jitter, at most N. After that the event goes to `dead_letters` with the error and an operator alert. A replay is an explicit operator command that is audited.
- **Delivery:** no exactly-once claims. Effects must be idempotent, and the inbox makes redelivery a no-op.
- **CONNECT** (PR #8) consumes through this mechanism instead of its own SQLite staging table. That table becomes BOOKS' export queue.

**Tests required.**
- crash after commit / before publish;
- crash after consume / before effect;
- duplicates;
- out-of-order events across aggregates;
- poison event;
- two workers racing;
- lease expiry;
- replay;
- cross-tenant isolation;
- v1 + v2 emitted in parallel.
