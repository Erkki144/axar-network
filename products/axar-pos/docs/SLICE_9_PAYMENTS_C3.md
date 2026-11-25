# Slice 9 — bill payments: split tenders, card (MOCK), transfer receivables (#15 C3)

Status: **local / CI only.** Branch `claude/axar-payments-c3-0r9ip8`, stacked on #24. Payments are **simulated**: the card terminal is `MockTerminalTxn`, bank lines are imported from a sandbox statement. No real card, bank or payment provider is called, and none can be configured.

## Four separate lifecycles (never "sale == payment")

| Object | States | Who moves it |
| --- | --- | --- |
| **Bill** (`orders`) | `open` → `partially_paid` → `paid` \| `invoiced` (voided later) | each tender; the bill row is locked first |
| **Payment** (`payments`) | cash: `captured` · card: `captured` \| `declined` \| `unknown` → reconcile → `captured`/`declined` · transfer: `pending` → `captured` on bank match | tender, reconcile, bank import |
| **Sale** (receipt, immutable) | issued **once**, when captured + pending-transfer payments cover the bill and no card answer is unknown | `payments._issue_sale` |
| **Customer invoice** (`customer_invoices`) | `issued` → `partially_paid` → `paid` | bank import |

VAT and revenue are recognized at the sale (receipt), not when the transfer arrives. `invoiced` = the bill is closed and receipted, the transfer part is still an open receivable. When the bank pays it, the bill moves `invoiced → paid` (event `order.changed` v2) and **no second sale** is written.

## Money rules

- Integer cents everywhere. Invariant after every step (tested): `paid + pending_transfer + in_flight_card + remaining == total`, `remaining >= 0`.
- **Cash rounding (5 c) only on the cash part that settles the final remainder.** A partial cash tender must be a multiple of 5 c; card and transfer are never rounded. Change comes only from cash tendered (a card tender above the remainder is `422 overpayment`).
- The device sends `expected_total_cents` (what it showed). A changed bill → `409 bill_changed`; a closed bill → `409 bill_closed`.
- Each tender has an `Idempotency-Key` bound to a request hash: same key + same body → the original payment (`200`, `Idempotent-Replayed: true`); same key + other body → `409 idempotency_key_reused`. The key is re-checked **after** taking the bill lock, so a parallel duplicate replays instead of being judged an overpayment.
- Lock order: **bill → drawer → counters** for split tenders *and* for the legacy one-shot `pay-cash` (which now locks the order row first), so the two paths cannot deadlock.
- A partially paid bill is **frozen** (lines cannot change; the legacy one-shot cash pay is refused) and stays the table's one bill: the partial unique index is now `status IN ('open','partially_paid')`.

## Drawer (cash) ledger

Only cash goes into the till. A captured cash tender adds `amount + rounding` to the drawer's running total and counts as one cash taking, at the register where it was taken (tenders of one bill may happen at different registers). `ledger_cash_sales` = direct cash sales (no payments) + captured cash payments; drawer close still refuses to close when the running total and the ledger disagree. The bill sale's `cash_due_cents` is the cash part only, so the daily report's cash column stays right. Sales paid with card/transfer (or mixed) **cannot be refunded in cash** (`422 invalid_refund`) — card/transfer refunds are a later slice.

## Bank matching (sandbox import)

`POST /api/v1/receivables/bank-import` (manager). Each line has the bank's own transaction id (unique per tenant → a re-imported line is `duplicate`), booked date, amount, reference, payer. A line matches **only** by an exact, checksum-valid Estonian reference number (*viitenumber*, 7-3-1) of the same tenant:

| Case | Result |
| --- | --- |
| exact amount | `matched`, invoice `paid`, transfer payment `captured`, bill `paid` |
| less | `partial`, invoice `partially_paid`, rest outstanding |
| more | `overpaid`: only the outstanding part is allocated; the surplus stays on the bank line for a person (never silently absorbed) |
| paid invoice paid again | `overpaid`, nothing allocated |
| unknown / invalid / other tenant's reference | `unmatched` |
| same bank transaction id again (also in parallel) | `duplicate` |

Bank lines are append-only (ORM guard + PostgreSQL grants: no UPDATE/DELETE).

## API

| Method | Path | Role |
| --- | --- | --- |
| GET | `/api/v1/orders/{id}/bill` | sale (cashier, waiter, manager) |
| POST | `/api/v1/orders/{id}/payments` (`Idempotency-Key`) | sale; `method` = `cash` (`tendered_cents`) \| `card` (`simulate` = `approved`/`declined`/`timeout`) \| `transfer` (`customer_name`, `customer_code`, `due_days`) |
| POST | `/api/v1/payments/{id}/reconcile` | sale; asks the MOCK provider about an `unknown` card payment (idempotent) |
| GET | `/api/v1/receivables/invoices` | manager (`receivables`) |
| POST | `/api/v1/receivables/bank-import` | manager (`receivables`) |

`/api/v1/operations/{key}` now also answers for payments (crash recovery).

## Events (contracts in `products/axar-one/architecture/contracts/events`)

- `pos.sale.completed` **v2** — one per settled bill: `tenders[]` (payment id, method, status, amount, rounding, invoice id), `currency: EUR`, `simulated: true`, `order_id`, `payment_method` `cash|card|transfer|mixed`. Identities checked by the contract test: Σ tender amounts = total; Σ tender rounding = cash rounding; cash_due = Σ cash tenders. `books.export` exports v1 and v2; `stock.consume` consumes v1 and v2 (lines are identical).
- `order.closed` **v2** (status `paid` \| `invoiced`, same transaction as the sale) and `order.changed` **v2** (`open`/`partially_paid`/`paid`). v1 payloads are unchanged.

## UI (AXAR ONE, tables)

"Võta sularaha" stays the quick one-shot path for a whole bill in cash. **"Jaga arve / kaart / ülekanne"** opens the bill dialog: total / paid / waiting for bank / card unknown / **remaining**, the list of tenders, method tabs (cash with tendered + change, DEMO terminal answer, payer name for a transfer), "Kogu jääk" / "Pool" shortcuts. A partially paid bill is marked on the table card ("Osaliselt tasutud") and its pay button continues in the dialog ("Maksa ülejäänu"). Each tender goes through the same crash-safe journal + Idempotency-Key as a sale: a lost answer is recovered after reload with the same key ("Lauaarve osamakse: Kaart 4,00 €"). An unknown card answer shows "Küsi terminalist" instead of asking the guest to pay again.

## Tests

- `tests/test_payments.py` (SQLite + PostgreSQL): the €85 split (20 cash + 45 card + 20 transfer → one sale, drawer close balances, bank match → paid), invariant after each step, cash rounding on the final cash part only, insufficient tender, double tap, changed/closed bill, frozen partially paid bill, open-drawer rules, declined card, unknown card + reconcile, partial/over/duplicate/unmatched bank lines, cross-tenant reference, v2 → books export and daily report, refunds, tenant isolation, and concurrency: 8 parallel devices on an €85 bill (exactly 4 × €20 succeed), 8 parallel same-key requests (one payment), 6 parallel final tenders (exactly one sale).
- `tests/test_event_contracts.py`: v2 schemas + financial identities; `tests/test_postgres.py`: append-only grants for the new tables; `tests/test_auth.py`: receivables are manager-only.
- `tests_e2e/test_bill_payments_ui.py` (browser): the €85 split, a partially paid bill continued, declined + lost card answer reconciled, lost tender answer recovered once after reload.

## Not in this slice

Real card terminal / acquirer, real bank API (PSD2), card and transfer refunds, voiding a bill, tips, splitting by items or seats, dunning, invoice PDF / e-invoice, accounting postings for receivables (export carries the data; accountant decision on GRNI/receivables is still open).
