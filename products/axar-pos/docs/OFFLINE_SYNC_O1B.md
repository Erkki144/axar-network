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
| Idempotency | Sale created with idempotency key `edge:<journal entry ULID>`; `device_inbox` is unique per (tenant, device, seq) | `test_lost_response_and_replayed_batches_never_duplicate` |
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
2. **Offline refunds** are quarantined for manual review; automatic refund sync
   is O3.
3. **Cash drawer** is per register on the central side; the till's drawer must
   be open centrally, otherwise the sale is quarantined and can be retried after
   the drawer is opened.
4. **72 h limit** is an owner decision; enforcement on the till (blocking sales
   after 72 h without a successful sync) is a separate setting slice.
5. The agent's HTTP transport is injected (`send(body)`); the production
   client for Windows/Android tills is part of the edge packaging work.

## Migration numbering

O1b uses migration `0006` (parent `0005`, from PR #49). The payments PR #26
currently also uses `0006`; whichever merges second is renumbered to `0007`.
`tests/test_migration_history.py` fails if two heads exist, so this cannot be
missed. Already-applied migrations 0001–0005 are not changed.
