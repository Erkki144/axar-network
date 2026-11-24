# ADR-0002 — Modular monolith on one PostgreSQL

Status: **Accepted and implemented** (#17: migrations, RLS, app role).

**Context.** #15 asks for module ownership without premature microservices. Invariants such as "a sale closes its order" and "a refund never exceeds the quantity sold" need single-database transactions.

**Decision.**
- One deployable app with module packages and one PostgreSQL.
- A module owns its tables and its write functions; other modules call those functions or react to events.
- SQLite is kept only for local demo and fast tests.

**Consequences.**
- No distributed dual writes.
- Module boundaries are enforced by code review and tests, not by the network.
- A module can be split later only behind its event contracts.
