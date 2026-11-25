# 07 — Failure and recovery matrix

| Failure | Today (evidence) | Target |
| --- | --- | --- |
| Response lost after a committed sale/refund/payment | client journal + same-key retry → replay (`test_crash_recovery*.py`, browser) | same; plus `GET /operations/{key}` |
| Two devices pay the same bill | one sale; the loser gets a definitive `order_changed` (`test_cross_device_orders.py`, PG `test_parallel_payment_of_same_order...`) | same for guest checkout (two tabs) |
| Sale vs drawer close race | close waits on the row lock and includes the sale (PG `test_close_waits_for_in_flight_sale...`) | — |
| Parallel refunds of one line | serialized per sale; exactly N succeed (PG test) | — |
| Kitchen send answer lost | same key → same ticket (browser test) | — |
| Session expired mid-operation | 401 locks the till; the journal resumes after login (browser test) | — |
| Brute force PIN/password | lockout per staff + device, 429 (`test_auth.py`) | DB-backed IP limiter |
| Crash between commit and event publish | impossible: event in the same transaction | relay resumes from its cursor (C1) |
| Crash between consume and effect | — (no consumer yet) | inbox row + effect in one transaction; a redelivery is a no-op (C1) |
| Poison event | — | bounded retries with jitter → dead letter + alert; replay after fix (C1) |
| Duplicate / out-of-order webhook | — | inbox dedup; state machine refuses backwards transitions (C3) |
| Notification channel down | — | in-page SSE / polling / pickup display still show status; staff sees "not delivered" (C5) |
| Server restart | stateless app; DB authoritative | SSE clients resume from `recorded_seq` cursor (C2) |
| DB restore | **never exercised (G-OPS-2)** | CI drill: dump → restore → migrate → invariants |
| Reconciliation mismatch | drawer close refuses on ledger mismatch (`LedgerMismatch`) | daily recon → quarantine + alert (ADR-0005) |

"No silent loss" rule:
- every failed effect ends either in a retry queue or in a visible exception queue with an owner;
- nothing is dropped without a record.
