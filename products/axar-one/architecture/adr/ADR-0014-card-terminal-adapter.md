# ADR-0014 — Provider-neutral card terminal adapter

Status: **Accepted for implementation (interface + sandbox).** No real provider is connected. The owner picks the provider later (#25).

## Decision

- **One contract.** The POS talks to card terminals only through `axar_pos/terminals/base.py::TerminalAdapter`, and the POS logic lives in `flow.py`.
- **One module per provider.** Each provider (Revolut, Nexi/Nets, Worldline, …) becomes one adapter module that maps its API onto this contract.
- **Contract tests gate adapters.** An adapter is accepted only if it passes `tests/test_terminal_contract.py` against the provider's **sandbox**.
- **Nothing provider-specific above the adapter.** The cashier UI, bills, payments, reconciliation and accounting see only the contract states.

| Contract rule | Why | Test |
| --- | --- | --- |
| Our `attempt_id` is the provider idempotency key; repeating it never charges again | double tap, retry after crash | `test_same_attempt_id_never_charges_twice`, `test_parallel_double_tap_with_one_attempt_id_charges_once` |
| Lost answer → `TerminalTimeout` → POS state **unknown**, resolved only by `get_status`; no new attempt until it is moneyless | the customer must never be charged twice | `test_lost_answer_is_resolved_by_status_query_not_guessed`, `test_flow_lost_answer_is_unknown_then_resolved_and_blocks_a_second_charge` |
| Not sent (terminal offline) → `TerminalUnavailable` → `NOT_FOUND`, a new attempt is safe | | `test_not_sent_means_not_found_and_nothing_charged`, `test_flow_not_sent_allows_a_new_attempt` |
| `status_query` capability is **required**; adapters without it are refused | without it rule 2 cannot hold | `test_flow_refuses_adapters_without_status_query` |
| Unsupported operations raise `CapabilityNotSupported`, never emulated | honest behaviour per provider | `test_unsupported_operations_raise_instead_of_being_emulated` |
| Refunds are idempotent and never exceed the approved amount | | `test_refund_is_idempotent_and_never_exceeds_the_approved_amount` |
| No card data: results carry at most brand + last 4; anything that looks like a card number is refused | PCI scope stays with the provider | `test_results_never_carry_card_numbers`, `test_flow_refuses_card_number_like_data` |

**Mutation check (2026-11-25).** Each of these mutants is caught by at least one test:
- removing idempotency;
- mapping a timeout to "declined";
- allowing a retry after money was taken;
- removing the refund cap.

## Mapping to POS states

| Terminal status | POS payment status (C3, PR #26) |
| --- | --- |
| `APPROVED` | `captured` |
| `DECLINED`, `CANCELLED`, `NOT_FOUND` | `declined` |
| `PENDING` or answer lost | `unknown` → reconcile via `flow.resolve` |
| not sent | `not_sent` (no payment row; a new attempt is allowed) |

PR #26 currently uses its own `MockTerminalTxn`. When #26 and this ADR are both merged, `payments.pay_card` / `reconcile_card` switch to `flow.charge` / `flow.resolve` with the adapter from settings. That is a small follow-up PR.

## Provider capability matrix — status: **NOT VERIFIED**

Every cell needs confirmation from the provider's documentation **and** sandbox before an adapter is written. Sources are the owner's research in #25. Nothing here means "connected" or "works".

| Capability | Revolut (Terminal, push payments) | Nexi / Nets (POS API) | Worldline (Terminal API / Baltics) | LHV |
| --- | --- | --- | --- | --- |
| Server-to-terminal purchase | documented ("Pay at Counter") | documented (cloud ECR API, simulated terminals) | documented; Baltic terminal flow is partner-specific | **not shown as an ECR terminal API.** LHV Connect covers bank statements and reconciliation |
| Status query by our id | documented (poll intent) | documented | to confirm | n/a |
| Cancel pending | documented | to confirm | to confirm | n/a |
| Linked refund | to confirm | to confirm | to confirm | n/a |
| Offline authorisation | to confirm (default: **off**, ADR-0012 §4) | to confirm | to confirm | n/a |
| Sandbox / simulator | to confirm access | documented public simulators | to confirm | — |
| Estonian merchant estate / terminal models covered | to confirm | **to confirm** (whether the API covers EE Nets/Estcard terminals) | to confirm (Worldline Baltics) | — |

**Before any real adapter:**
1. Owner choice of provider.
2. Merchant agreement.
3. Sandbox credentials: stored as an environment network secret, never in the repo.
4. The adapter passes the contract suite against the sandbox.
5. A hardware-in-the-loop test with the physical terminal model.
6. Owner approval to enable it.
