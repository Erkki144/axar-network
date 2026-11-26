# O1b: offline till sync (edge → central)

Status: draft PR, built on O1a (device journal, PR #49). Payments stay simulated.
Design source: ADR-0012 §7 (offline-first). Owner decisions: offline for at most
72 h, tills run on Windows and Android, per-register receipt numbers (`K1-000123`).

## What it does

A till keeps selling when the network is down. Every sale or refund on the till
writes, **in the same database transaction**:

1. the document (sale/refund) itself,
2. a hash-chained journal entry (`device_journal`, O1a),
3. a `sync_outbox` row holding the portable payload and its SHA-256 hash.

If any part fails, none of it is written, so the outbox never holds a sale that
does not exist (and the reverse).

The **sync agent** (`axar_pos/sync_agent.py`) pushes pending outbox rows to the
central server in strict `seq` order, in batches of up to 50. The central
server (`axar_pos/sync.py`, `POST /api/v1/sync/push`) ingests each entry in one
transaction.

```
till                                   central
----                                   -------
sale + journal + outbox  (1 txn)
agent: batch of pending rows  ───────► per entry, 1 txn:
                                         lock device cursor
                                         check seq, prev_hash, entry hash, payload hash
                                         apply (sale with key edge:<entry id>) or quarantine
                                         inbox row + advance cursor, commit
       ◄────── acked_through + per-entry result
mark rows acked / quarantined / rejected
```

## Guarantees and how they are enforced

| Requirement | Mechanism | Test |
|---|---|---|
| Idempotency | Sale created with idempotency key `edge:<journal entry ULID>`; `device_inbox` is unique per (tenant, device, seq) | `test_lost_response_and_replayed_batches_never_duplicate`, `test_retry_never_sells_twice_even_if_the_sale_already_exists` |
| No duplicates | Re-sent entry with the same hash → `duplicate`, nothing changes; same seq, different content → `conflict` (audited, agent stops) | same + `test_forged_entry_and_reused_seq_are_refused` |
| Parallel pushes | The device cursor row is locked (`SELECT … FOR UPDATE`) per entry; after a quarantine rollback the cursor is re-checked | `test_two_agents_pushing_the_same_device_in_parallel_apply_each_sale_once` (runs on PostgreSQL in CI) |
| Order | An entry that does not follow the cursor is a `gap`; nothing after it is processed; the agent resends from `acked_through` | `test_out_of_order_entries_are_not_applied_until_the_gap_is_filled` |
| Integrity | Broken `prev_hash`, entry hash or payload hash → `chain_broken`, audited, the stream stops | `test_tampered_payload_breaks_the_chain_and_blocks_the_agent` |
| Review queue for bad entries | Entries that cannot be applied are acknowledged as `quarantined` (stream moves on) and wait for a manager: `GET /api/v1/sync/quarantine`, `POST …/{id}/retry`, `POST …/{id}/dismiss` (reason ≥ 5 characters, audited) | `test_unappliable_sales_are_quarantined_not_lost_then_retried_once`, `test_price_mismatch_is_quarantined_and_can_be_dismissed_with_a_reason` |
| Recovery when the connection returns | Network error / timeout / 5xx / 429 changes nothing locally; retry with exponential backoff + jitter (2 s … 5 min); a lost response is resent and comes back `duplicate` | `test_offline_sales_sync_exactly_once_after_the_network_returns`, `test_server_errors_are_retried_with_growing_backoff` |
| Access | A device can push only its own journal; the quarantine queue needs the `Reporter` permission (manager) | `tests/test_auth.py::test_sync_push_only_for_the_devices_own_journal_and_quarantine_needs_a_manager` |

Rows are never deleted on either side: acked outbox rows and inbox rows stay for
audit (retention policy is O5).

### Quarantine reasons (examples)

- unknown or inactive register / product,
- drawer closed or another domain rule refused the sale,
- `price_mismatch`: the till charged a total that central prices do not give,
- refund made offline (see limitations),
- invalid payload.

`retry` re-applies with the **same** idempotency key, so a retry never sells
twice. `dismiss` records who, when and why.

## Configuration

| Variable | Meaning |
|---|---|
| `AXAR_POS_EDGE_DEVICE_ID` | Set on a till: this installation is an edge node and writes `sync_outbox` rows for this device. Unset on the central server. |

Migration `0006_offline_sync` adds `sync_outbox`, `device_cursors`,
`device_inbox`, with row-level security on `tenant_id` and `SELECT, INSERT,
UPDATE` (no `DELETE`) for the application role.

## Known limitations (O1b)

1. **Central prices.** A sale is applied at central prices. If they differ from
   what the till charged, the sale is quarantined, not recorded "as sold".
   Recording at the snapshot price comes with the signed price snapshot (O2).
2. **Offline refunds** sync automatically since O3 (see below). Table bills paid
   offline (split/card/transfer) still go to the review queue.
3. **Cash drawer** is per register on the central side; the till's drawer must
   be open centrally, otherwise the sale is quarantined and can be retried after
   the drawer is opened.
4. **72 h limit** — done in O1c, see below.
5. The agent's HTTP transport is injected (`send(body)`); the production
   client for Windows/Android tills is part of the edge packaging work.

## Migration numbering

O1b uses migration `0006` (parent `0005`, from PR #49). The payments PR #26
currently also uses `0006`; whichever merges second is renumbered to `0007`.
`tests/test_migration_history.py` fails if two heads exist, so this cannot be
missed. Already-applied migrations 0001–0005 are not changed.

## Verification (this PR)

- `tests/test_offline_sync.py`: 14 tests, SQLite and PostgreSQL, including a seeded soak test: 72 h of offline selling (>100 sales) with random network faults (down, 503, response lost after commit) and clock jumps; afterwards every sale is on the central server exactly once with equal totals and the outbox is fully acked.
- Full suites: SQLite 277 passed / 14 PostgreSQL-only skipped; PostgreSQL 291 passed;
  migrations up/down/up + `alembic check` clean; browser e2e 44 passed; bandit clean.
- Mutation check (each change must make a test fail): duplicate check ignoring the
  hash, no gap check, a lost response treated as acked, no payload-hash check,
  retry with a fresh idempotency key - all 5 caught.

## O1c: offline limit (owner decision: at most 72 h, set in the POS settings)

| Rule | How | Test (`tests/test_offline_limit.py`) |
|---|---|---|
| A till may sell without the central server for at most `offline_limit_hours` | every NEW sale/refund on an edge node runs `offline.guard`; past the limit it is refused with `423 offline_limit_exceeded` | `test_sales_stop_after_72h_without_contact_and_restart_when_the_connection_returns` |
| Nothing already sold is affected | the guard runs after the idempotency check: a retry of an existing sale still returns it; the outbox keeps syncing | same |
| Contact = any 200 answer from the central server | the sync agent records it (`edge_sync_state.last_contact_at`) with the server's current limit; with nothing pending it sends a heartbeat (empty push) every 5 min | `test_a_heartbeat_renews_the_window_when_nothing_is_pending`, `test_failed_contact_does_not_extend_the_window` |
| Limit set by a manager in the POS settings | `GET/PUT /api/v1/admin/pos-settings` `{offline_limit_hours: 1..72}`; owner or tenant-wide manager with MFA; audited (`pos_settings.changed`, before/after) | `test_manager_sets_the_limit_and_the_till_gets_it_on_the_next_contact`, `tests/test_auth.py::test_offline_limit_setting_needs_an_owner_or_tenant_wide_manager` |
| The till never accepts more than 72 h | values from the server are clamped to 1..72 | `test_a_server_cannot_raise_the_limit_above_72h` |
| The clock cannot be set back to dodge the limit | the till keeps the latest time it used (`max_seen_at`); a clock more than 5 min behind it blocks selling (`clock_moved_back`) until fixed | `test_setting_the_clock_back_blocks_selling` |
| A till must have been online once | no contact record → selling refused | `test_a_till_that_never_connected_cannot_sell` |
| The central server is never limited | the guard is a no-op without `AXAR_POS_EDGE_DEVICE_ID` | `test_the_central_server_is_never_limited` |

`GET /api/v1/sync/status` (edge only) tells the till UI whether it may sell, the deadline and the remaining seconds.

### Till UI (O1d)
- **Warning banner** (yellow) when 12 h or less remain: "Keskserveriga pole ühendust. Kassa lukustub 8 h 59 min pärast (piirang 72 h), kui ühendus ei taastu. Müügid on alles."
- **Lock banner** (red) with the reason: limit exceeded, clock set back, or never connected.
- Refreshed every 60 s and immediately after a refused sale. A central (non-edge) till asks once, gets 404 and stops.
- A refused sale shows "Makset ei salvestatud: Kassa on lukus …" and keeps the basket.
- `offline_limit_exceeded` / `clock_moved_back` are treated as **definitive** (nothing recorded): the server checks the idempotency key before the offline guard, so a recorded sale would have been replayed. The payment journal is cleared instead of being left "unknown".
- Tests: `tests_e2e/test_offline_banner.py` (warning + selling works, lock + basket kept + selling after contact, clock set back, no banner centrally).

Migration `0007_offline_limit`: `pos_settings` (central) and `edge_sync_state` (edge), RLS + grants without DELETE.

## O3: offline refunds sync automatically

| Rule | How | Test (`tests/test_offline_refunds.py`) |
|---|---|---|
| A refund made on the till is applied centrally to the central copy of its original sale | the till's refund payload carries the original sale's journal entry id; the central server finds that entry in `device_inbox` and refunds the sale it created | `test_offline_sale_and_partial_refunds_sync_exactly_once` |
| Never refunded twice | idempotency key `edge:<refund entry id>`; inbox dedup per device+seq; a retry of an already-applied refund links to it | same + `test_retry_never_refunds_twice_even_if_the_refund_already_exists` |
| Order is respected | a refund of a sale that is still quarantined (or dismissed) is quarantined too; once the sale is retried, the refund can be retried | `test_refund_waits_for_its_quarantined_sale_and_applies_once_after_retry` |
| Amounts must match | the central refund total must equal what the till paid out, otherwise `price_mismatch` → review queue, nothing applied | `test_refund_whose_amount_differs_is_quarantined_not_applied` |
| Same rules as at the till | manager/owner role required; open central drawer with enough cash; never more units than sold | existing refund rules in `services.create_cash_refund` |

`device_inbox.sale_id` holds the id of the document an entry created (a sale or a refund).
Mutation check: 4 of 5 deliberate bugs caught; the 5th (dropping the status check) is equivalent, because a quarantined or dismissed sale never has a central sale id.
