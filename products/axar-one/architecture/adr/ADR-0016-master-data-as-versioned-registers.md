# ADR-0016 — Master data as versioned, event-emitting registers

Status: **Proposed** (owner review). Related: ADR-0003, ADR-0004, ADR-0005, `12-register-map-and-journeys.md`.

**Context.**
- Transactions (sales, refunds, drawer, orders, stock receipts) already emit events and are reconciled (R1–R13).
- Master data (product, price, recipe, discount, payment method, order type, POS layout, staff, warehouse, supplier) is changed in place
  or only seeded. Nothing reacts to those changes, tills cannot learn about them in a controlled way, and history depends on snapshots alone.

**Decision.**
1. Every master-data register has one owner module and is changed **only** through that module's commands (office or platform UI → API).
2. Changes that affect money, stock or tills are **versions** with `valid_from` (and optional `valid_to`); the previous version stays.
   Applies to: price, recipe, discount rule, POS layout, tax rate, permission group.
   Descriptive data (name, address, contact) is updated in place with an audit entry.
3. A publish writes the version and a `<module>.<entity>.<verb>` event (envelope v2) **in the same transaction**.
4. Before publishing, the API returns an **impact preview**: affected outlets, tills, open orders / documents, margin change. Blocking conflicts
   (e.g. deactivating a product on an open order) are refused with the list.
5. Tills keep a versioned master-data cache; the sync agent pulls changes since the last version on every contact. Offline sales carry the version
   they used and are accepted as facts (J9).
6. Referenced master data is never deleted; it is deactivated.
7. Each new versioned register adds a reconciliation rule (e.g. "sale line price = valid price version or approved manual price") and a tamper test.

**Consequences.**
- One more table per versioned register and one event type each; additive migrations only.
- Office screens need a draft → preview → publish flow instead of plain save.
- Reports and audits can answer "what was the price / recipe / permission at that moment" from data, not from guesswork.
- Implemented incrementally: products + prices in P1-1, discounts / order types / payment methods in P1-5, permission groups in P1-8.
