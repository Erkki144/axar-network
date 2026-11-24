# Slice 4 — server-side table bills, kitchen tickets, operation status

Status: **local demo, simulated cash only.** Branch
`claude/axar-one-s6-orders-0r9ip8`, stacked on PR #12. Nothing is deployed.
The demo header identity is still not authentication (RFC-0002 / #2).

## Why

In slice 3, table bills and kitchen tickets lived only in one browser's
`localStorage`. A waiter's phone and the counter tablet could not see the same
bill, and a bill was lost if the device was replaced. Now the server owns them.

## Data model

| Table | Purpose | Key constraints |
| --- | --- | --- |
| `dining_tables` | tables of a location | unique `(tenant, code)` |
| `orders` | one bill | `status open\|paid`, `version`, `sale_id` unique; partial unique index: **one open order per table** |
| `order_lines` | product + quantity on a bill | unique `(order, product)`; `sent_quantity` ≤ `quantity` |
| `kitchen_tickets` | one "send to kitchen" | unique `(tenant, idempotency_key)`, `(tenant, ticket_no)` |
| `kitchen_ticket_lines` | what was sent | append-only (ORM guard) |

## API

| Call | Behaviour |
| --- | --- |
| `GET /api/v1/tables` | tables + summary of the open bill (`id`, `version`, `item_count`) |
| `POST /api/v1/tables/{id}/order` | get-or-create the open bill (safe from many devices at once) |
| `GET /api/v1/orders/{id}` | the bill |
| `PUT /api/v1/orders/{id}/lines` `{version, lines}` | replace lines; **409 `order_changed`** if `version` is not current; 422 below sent quantity |
| `POST /api/v1/orders/{id}/kitchen` `{version}` + `Idempotency-Key` | ticket with only the unsent quantities; same key → same ticket (200) |
| `GET /api/v1/kitchen/tickets`, `POST /api/v1/kitchen/tickets/{id}/ready` | kitchen view; ready is idempotent |
| `POST /api/v1/orders/{id}/pay-cash` `{version, register_id, tendered_cents}` + `Idempotency-Key` | sale from the bill's lines **and** bill → `paid` in ONE transaction |
| `GET /api/v1/operations/{key}` | `recorded` (sale/refund id) or `not_recorded` for this tenant |

### Invariants (each has a test)

- Every edit is a compare-and-swap on `version`: a stale device gets 409 and
  never silently overwrites another device's change.
- Paying uses the version the cashier saw. Closing the bill is a conditional
  `UPDATE … WHERE status='open' AND version=?` inside the sale transaction; if
  it does not match, the sale is rolled back. Eight parallel payments of one
  bill → exactly one sale (`test_parallel_payment_of_same_order_from_many_devices`).
- The idempotency key is looked up before the order check, so a same-key retry
  of a recorded payment replays the sale. Hence `order_changed` on a retry
  proves "not recorded" and the UI treats it as definitive.
- The request hash of an order payment includes `order_id` and `order_version`
  (`hash_extra`); hashes of plain sales are unchanged.
- Quantities already sent to the kitchen cannot be reduced (422).

## UI (AXAR ONE)

- Tables mode loads tables from the server. Choosing a table opens its bill
  (get-or-create). Opening and every edit go through one queue, so fast taps
  are applied in order to the latest version.
- On 409 the UI reloads the bill, shows "Arvet muudeti teises seadmes…" and
  drops the taps still queued (they were meant for the old bill).
- Tables and kitchen views poll every 4 s (not while the tab is hidden, and
  the bill is not reloaded while a payment is pending).
- "Send to kitchen" keeps its key until a definitive answer, so a retry after
  a lost answer cannot create a second ticket.
- Paying a table bill uses the same journaled `runMoneyOperation` as a walk-in
  sale (path `/api/v1/orders/{id}/pay-cash`, cart key `order:{id}`). The
  journal schema accepts that path only with `{version, register_id,
  tendered_cents}` and a matching cart key; old `table:r:i` records from
  slice 3 are still accepted so they can be resolved.
- The recovery screen also shows the server's status for the key
  (informational; the way out is still the same-key retry).

## Tests

- Backend: `tests/test_orders.py` (13). Mutation check: removing
  `status`/`version` from the close-order `WHERE` fails 3 tests.
- Browser: `tests_e2e/test_cross_device_orders.py` (5). Each device is a
  **separate browser context** (own storage, own Web Locks), so only the server
  can coordinate them:
  - bill started on device A is seen and extended on B, A picks it up by polling;
  - a stale device cannot overwrite and is told;
  - both devices pay the same bill at the same instant → one sale, the other
    gets a clear error and is not blocked;
  - a kitchen ticket from the waiter device appears on the kitchen device;
  - a lost kitchen answer + retry → same key, one ticket.
- UI mutation checks (each made a test fail): `order_changed` not definitive;
  a new kitchen key on every attempt; a 409 on an edit not handled.
- `tests_e2e/test_crash_recovery_round2.py`: a table payment whose answer was
  lost is resolved once after a reload.

## Known limits

- Identity is still demo headers; anyone who can reach the port can act as
  any tenant. Do not expose beyond `127.0.0.1` (RFC-0002).
- Polling, not push. Fine for a few devices; websockets/SSE later.
- No split bills, moving bills between tables, or voiding sent items.
- PostgreSQL concurrency is argued (conditional UPDATEs) but only SQLite is
  tested in CI.
