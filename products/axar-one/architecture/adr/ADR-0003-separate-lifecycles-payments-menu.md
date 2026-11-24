# ADR-0003 — Separate order, payment, fulfilment, stock and accounting lifecycles

Status: **Proposed** (fixes G-DM-1/2/3).

**Decision.**
- **Payments** become their own aggregate (tender or payment intent) with the lifecycle in `05-state-machines.md`.
- A sale/receipt references one or more succeeded tenders. Cash stays an immediate tender.
- **Fulfilment** is tracked per ticket line and station, independent of payment.
- **Menu offer / variant / modifier / price version** is split from the inventory item (#14). Order lines snapshot offer, price and tax version.
- Migration path: add the new tables, then write both shapes, then switch reads, then remove old columns. Each step is a reversible Alembic migration with tests.

**Consequences.** This enables split checks, mixed tenders, pay-before-prepare (#18), partial readiness and explicit waste.
