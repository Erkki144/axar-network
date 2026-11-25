# 08 — Requirement → test traceability

Status:
- ✅ = covered by an automated test that runs in CI;
- ◐ = partially covered;
- ☐ = not built.

Test paths are relative to `products/axar-pos/`.

| Req (issue) | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| #3 | append-only sales/refunds, versioned VAT, idempotency, business-day cutoff | ✅ | `tests/test_sales.py`, `test_refunds.py`, `test_refund_allocation.py`; PG grants `tests/test_postgres.py` |
| #3 | daily reconciliation | ◐ | drawer ↔ ledger at close only (`LedgerMismatch`); ADR-0005 |
| #2 | tenant isolation incl. RLS | ✅ | `test_tenancy_and_identity.py`, `test_postgres.py::test_rls_*`, `test_auth.py::test_tenant_comes_from_the_session...` |
| #2 | roles, no implicit demo identity | ✅ | `test_auth.py::test_role_matrix`, `test_no_implicit_demo_identity_by_default` |
| #2 | PIN login, MFA managers, step-up | ✅ | `test_auth.py` (32), `tests_e2e/test_server_auth.py` |
| #2 | PostgreSQL + Alembic, concurrency on PG | ✅ | CI `test-postgres`, `test_postgres.py` (lock interleavings) |
| #11 | UI on the real API, retries never double-charge | ✅ | `tests_e2e/test_cashier_ui.py`, `test_crash_recovery*.py` |
| #11 | table bills across devices | ✅ | `tests_e2e/test_cross_device_orders.py`, `tests/test_orders.py` |
| #11 | click-count / latency benchmarks | ◐ | `AXAR_E2E_REPORT` timing hooks; no target thresholds yet |
| #15 | versioned event contracts | ◐ | v1 schemas + producer test `tests/test_event_contracts.py`; v2 envelope proposed |
| #15 | outbox → inbox, retries, DLQ, replay | ☐ | ADR-0004 (C1) |
| #15 | workflows, process registry, rules registry | ☐ | after C1 |
| #15 | observability, restore drill | ☐ | G-OPS-1/2 |
| #8 ↔ POS | producer/consumer contract | ◐ | manual run: 64/64 real events accepted by CONNECT; automated cross-module test needs an integration branch (G-ENG-1) |
| #14 | stock ledger, recipes, costing | ☐ | C4 |
| #16 | operating modes matrix | ☐ | after C1–C5; shared primitives first |
| #18 | QR guest ordering, sandbox payment, live status | ☐ | C3 + C5; ADR-0006 |
| #10 | usability validation with 20 operators | ☐ | needs pilot hardware/people (owner) |

## Measurable targets proposed for the next slices (to be confirmed by the owner)

| Metric | Target | Measured how |
| --- | --- | --- |
| POST /sales/cash p95 (local PG, 1 register) | < 150 ms | load script, 1,000 sales |
| 20 tills × 1 sale/s for 5 min | 0 errors other than designed 409s; receipt numbers gap-free | PG load test |
| Event relay lag p95 | < 2 s | `recorded_seq` vs consumer cursor timestamps |
| SSE status update after a ticket becomes ready, p95 | < 1 s (same region) | browser test with two contexts |
| Guest checkout double-tap / two tabs | exactly 1 payment intent | browser test |
