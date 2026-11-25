# AXAR ONE — architecture baseline v0.1

Owner directive: #15 (whole-organism review), with #16 (operating modes) and #18 (guest ordering).

This is deliverable (1): the actual gap audit, ADRs, contracts and dependency DAG. It describes **code that exists**, with evidence, and separately what is proposed. Nothing proposed here is implemented unless a test is named.

| Audited | Commit |
| --- | --- |
| POS stack (#7→#12→#13→#17→#19) | `f25f03b` |
| AXAR CONNECT (#8) | `2e03643` |
| UX prototype (#9) | `6c37a5d` |

| File | Content |
| --- | --- |
| [01-system-context.md](01-system-context.md) | context diagram, modules, dependency rules, delivery DAG |
| [02-gap-audit.md](02-gap-audit.md) | **findings with evidence and priority**, executed checks |
| [03-data-ownership.md](03-data-ownership.md) | tenant/legal entity/location boundaries, single-writer table ownership, canonical IDs |
| [04-event-catalog.md](04-event-catalog.md) | emitted and planned events, versioning rules |
| [05-state-machines.md](05-state-machines.md) | drawer/order today; order, payment, fulfilment, notification, export targets |
| [06-trust-and-permissions.md](06-trust-and-permissions.md) | trust boundaries, secrets, PII |
| [07-failure-and-recovery.md](07-failure-and-recovery.md) | failure → today's evidence → target |
| [08-requirements-to-tests.md](08-requirements-to-tests.md) | issue requirements → tests; proposed measurable targets |
| [09-v1-1-offline-hierarchy-plan.md](09-v1-1-offline-hierarchy-plan.md) | **v1.1**: offline-first (#29), hierarchy (#27), hardware (#25) — gaps and delivery order |
| [adr/](adr/) | ADR-0002 … ADR-0013 (0012 offline-first edge node supersedes 0010; 0013 hierarchy + migration contract) (ADR-0001 = `products/axar-pos/docs/ARCHITECTURE.md`) |
| [contracts/events/](contracts/events/) | JSON Schemas: v1 payloads (enforced by `products/axar-pos/tests/test_event_contracts.py`), v2 envelope (proposed) |

**Open owner decisions:**
1. Private repository (G-SEC-1).
2. Confirm the C1→C5 order.
3. Measurable targets in `08`.
4. An integration branch that combines the POS stack with AION's #8 for an automated cross-module test (G-ENG-1).
5. Accountant validation of VAT, rounding and the account mapping.
