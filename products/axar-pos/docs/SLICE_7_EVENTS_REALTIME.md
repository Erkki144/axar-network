# Slice 7 — outbox v2, consumers, realtime sync (ADR-0004; #2 "reaalajas sünkroonimine"; #15 C1)

Status: **local / CI only.** Branch `claude/axar-pos-s9-events-0r9ip8`, stacked on #20.

## What changed

| Area | Change |
| --- | --- |
| Outbox v2 | Every event has `recorded_seq`, a gap-free per-tenant sequence. It also has the aggregate type/id/version, actor (staff, device, session from the authenticated session), location, and correlation = request ID or the sale's correlation. The payload `version` keeps its meaning, so v1 consumers (CONNECT #8) are unaffected. |
| Ordering | `events.emit` queues on the session. A `before_commit` hook writes the queued events **after every other write** of the transaction and takes the tenant's `event` counter **last**. Sequence order therefore equals commit order, so a cursor can never skip an event, and there is no deadlock cycle with drawer/order locks. A rollback drops the queue (tested, including the same-session case). |
| New events | `drawer.opened/closed`, `order.opened/changed/closed`, `kitchen.ticket.created/ready`, each with a committed JSON Schema and checked against real events in `tests/test_event_contracts.py`. `order.closed` is in the same transaction as its `pos.sale.completed`. |
| Consumers | `events.run_consumer`: per (consumer, tenant) lease with a **fencing token**. For each event, in one transaction: inbox row (dedup) + effect + cursor (only while the token is current). Failure → retry with exponential backoff and jitter. After 5 attempts → `event_dead_letters` and the consumer continues. Operator replay is idempotent. `GET /api/v1/admin/event-health` shows lag and open dead letters. |
| Realtime | `GET /api/v1/events/stream`: server-sent events, invalidations only (ids + versions, never payload data), tenant- and location-scoped. Resume with `Last-Event-ID` / `?after=`. Heartbeat; the session is re-checked every 15 s (revoked → `event: auth`); max duration with reconnect. It does not hold a DB connection while idle. |
| UI | AXAR ONE reads the stream with `fetch` (EventSource cannot send the auth headers). On each frame it re-reads the affected view. It reconnects with its cursor and exponential backoff. Polling stays as a 30 s safety net, or 4 s while the stream is down. The status pill shows "reaalajas" when live. |
| Tracing | `X-Request-ID` (accepted or generated) on every response, used as the event correlation. One JSON log line per request with route, status and ms; no bodies, tokens or PINs. |
| PostgreSQL | Migration `0003`: v2 columns, backfill of `recorded_seq` for existing events in historical order plus the counter, RLS on the consumer tables. The app role may only `SELECT, INSERT` the inbox and cannot delete dead letters. |

## Tests

- `tests/test_events.py` (11), on both databases:
  - rolled-back transactions emit nothing, and nothing leaks into a later commit on the same session;
  - actor and correlation are recorded;
  - sequences are per tenant;
  - exactly-once *effect* with redelivery as a no-op;
  - a failed effect is rolled back and retried with backoff;
  - a poison event goes to the dead-letter queue, the consumer continues, and replay works;
  - a stale worker cannot move the cursor after losing its lease;
  - tenant isolation;
  - SSE resume and the "no payload in the feed" rule;
  - SSE head start and tenant scope.
- `tests/test_event_contracts.py`:
  - all 9 event types validate against committed schemas;
  - sequence 1..n with no gaps;
  - per-aggregate ordering;
  - sale before `order.closed`.
- Browser `test_realtime_stream_updates_other_device_without_polling`:
  - the kitchen device does not poll, and the waiter's ticket arrives through the stream in **< 3 s** (asserted; measured value recorded in the e2e report);
  - a bill change reaches a second waiter device the same way.
- **Mutation checks** (each mutant made a test fail):
  - fencing removed;
  - queued events kept after rollback (this one survived first, so a test was added);
  - events dropped;
  - inbox dedup removed.

## Limits

- Each open stream polls the DB every 0.5 s with one indexed query. That is fine for tens of devices per site. For hundreds, add PostgreSQL `LISTEN/NOTIFY` fan-out; the interface stays the same.
- No real consumer is registered yet. CONNECT (AION, #8) is the first intended one, through `run_consumer`. A scheduler/worker process for consumers is the next step.
- Kitchen stations and the full fulfilment lifecycle are still future work (ADR-0003).

## AION independent review (comment on #21) — reproduce, fix, mutation-check

| # | Finding | Reproduced by (failed before the fix) | Fix | Mutant killed |
| --- | --- | --- | --- | --- |
| 1 | PIN/password failure counters: read-modify-write, lost increments | PG: 12 parallel wrong PINs → counters < attempts and **all 12 evaluated** (`test_concurrent_wrong_pins_are_all_counted`, `test_concurrent_wrong_passwords_lock_after_the_limit`) | device row, then staff row `FOR UPDATE` + re-read before the lockout check (#19) | removing the locks fails both (PG) |
| 2a | stream re-check / reconnect renewed the session, so an idle till never locked | `test_open_or_reconnecting_stream_does_not_keep_an_idle_till_unlocked` | `touch=False` for the stream (dependency and re-checks) | ✓ |
| 2b | stream used the original principal; a location-bound back-office manager saw every location | `test_stream_of_location_manager_back_office_session_is_location_scoped` | refreshed principal on every re-check; `Principal.visible_locations()`; ends if no roles are left | ✓ |
| 3 | events were not savepoint-scoped; `before_commit` fires on savepoint release | `test_event_emitted_in_rolled_back_savepoint_is_not_written`, `test_released_savepoint_events_die_with_the_outer_rollback` | queued events remember their (sub)transaction; a nested rollback drops its own; written only at the outermost commit (also asserted: nothing written at release → lock ordering) | ✓ (both variants) |
| 4 | dead letter advanced the cursor → later events applied before an earlier one | `test_block_policy_keeps_order_for_financial_consumers` | per-consumer `on_poison`: **`block` (default)** stops at the poison event until replayed/resolved; `skip` only when order does not matter | ✓ |
| 5a | any `IntegrityError` on the inbox insert was treated as a duplicate (event silently skipped) | `test_non_duplicate_integrity_error_is_a_failure_not_a_skip` | duplicate only if the inbox row exists; otherwise a failure with retry | ✓ |
| 5b | raw exception text stored in health/dead letters (could hold SQL parameters or secrets) | `test_errors_are_redacted_in_health_and_dead_letters` | only `Type/RootType fp=<hash>` is stored | ✓ |
| 5c | retry branch ignored the fencing rowcount; the lease was kept after a failure | `test_failed_run_releases_its_lease_so_another_worker_can_retry` | fencing checked on every write; lease released on every early return | ✓ |
| 6 | no real consumer and no worker | `test_worker_stages_financial_events_for_books_exactly_once` (3 workers in parallel + rerun), `test_corrupt_financial_event_blocks_books_export_instead_of_skipping` | `books.export` consumer (block policy) → dedicated `export_queue` outbox, no external effect in the handler; `python -m axar_pos.worker [--once]` | — |
| + | pilot guard (G-SEC-2) | `test_pilot_profile_refuses_unsafe_configuration` | `AXAR_POS_PROFILE=pilot` refuses demo identity / SQLite / same app+owner connection / missing key, before any DB access (#19) | — |
