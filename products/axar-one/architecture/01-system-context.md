# 01 — System context, modules and dependency DAG

## System context

```mermaid
flowchart LR
  staff[Staff: cashier, waiter, kitchen, manager, owner] -->|PIN on enrolled device / password+TOTP| ONE[AXAR ONE web UI]
  guest[Guest phone] -->|QR, PWA - planned #18| GUEST[AXAR GUEST]
  ONE -->|HTTPS JSON, Bearer + device token| CORE
  GUEST -->|scoped opaque order token - planned| CORE
  subgraph CORE[AXAR modular monolith - one PostgreSQL]
    IAM[identity & access] --- POS[sales, drawer, refunds]
    POS --- ORD[orders & fulfilment]
    ORD --- KDS[kitchen tickets]
    POS --- PAY[payments - planned]
    ORD --- MENU[menu & prices - planned]
    MENU --- STOCK[inventory & costing - planned #14]
    OUTBOX[(outbox / inbox)]
  end
  CORE -->|events v1/v2| BOOKS[AXAR CONNECT: accounting previews PR #8]
  PAY -.sandbox only.-> PSP[payment provider sandbox]
  BOOKS -.preview only.-> ACC[Merit Aktiva etc - not connected]
```

Trust boundaries:
- browser ↔ API (untrusted input; server authoritative);
- provider webhooks (untrusted until signature verified);
- CONNECT ↔ external books (not connected).

See `06-trust-and-permissions.md`.

## Logical modules (capabilities, not services)

| Module | State | Code today |
| --- | --- | --- |
| IAM | **built** (slice 6) | `axar_pos/auth.py`, migration 0002 |
| POS (sale, drawer, refund) | **built** | `axar_pos/services.py` |
| ORDERS / KDS | **built**, basic (`open/paid`, `new/ready`) | `axar_pos/orders.py` |
| OUTBOX | write-only v1 | `models.OutboxEvent` |
| BOOKS (CONNECT) | previews, separate package | PR #8 `products/axar-connect` |
| UI (AXAR ONE) | built on the API | `axar_pos/static/one` |
| PAY, MENU, STOCK, GUEST, NOTIFY, WORKFLOW, RECON | **not built** | — |

## Module dependency rules

- Writes are owned by one module (see `03-data-ownership.md`). Other modules call its service functions (synchronous command, same transaction where an invariant needs it) or react to its events (asynchronous, through the inbox).
- **No cycles.** The allowed direction is IAM ← everything; MENU ← ORDERS ← POS/PAY; STOCK reacts to events from ORDERS/POS/PAY; BOOKS reacts to POS/PAY/STOCK; NOTIFY and GUEST react to ORDERS/KDS.
- Read models (KDS screen, guest status, reports) are derived and rebuildable from authoritative tables plus events.

## Delivery DAG (draft PRs)

```mermaid
flowchart TD
  S5[#17 PostgreSQL + RLS] --> S6[#19 identity]
  S6 --> A[this PR: baseline, ADRs, contracts]
  A --> C1[C1 outbox v2 + relay + inbox + observability]
  C1 --> C2[C2 order/fulfilment events + SSE + stations]
  C1 --> C3[C3 payments aggregate + sandbox PSP + webhook inbox]
  C2 --> C5[C5 GUEST vertical slice #18]
  C3 --> C5
  C1 --> C4[C4 menu offer, price version, item master, stock ledger #14]
  C4 --> C5
  C1 --> BK[BOOKS consumes via inbox, AION #8]
  C5 --> P[operating profiles #16, hardware/offline, pilot readiness]
```

Each node has measurable acceptance criteria in `08-requirements-to-tests.md`.
