# ADR-0012 — Offline-first POS: edge node with durable local SQLite and two-way sync

Status: **Proposed** (owner directive #29, P0). **Supersedes ADR-0010.** Nothing in this ADR is implemented yet; each claim below says how it will be tested.

## Context

#29 makes offline operation a hard requirement. The till must keep selling, store everything durably on the device, and synchronize both ways when the network or central database returns.

What the code does today, from the audit:

| Area | Today | Problem for #29 |
| --- | --- | --- |
| Domain services | `axar_pos/services.py`, `orders.py`, `payments.py`, `inventory.py` run on **both SQLite and PostgreSQL**; the whole test suite runs on both | none — this is the asset to reuse |
| Where the UI runs | the browser talks to the central API; it keeps a crash journal in `localStorage` (`app.mjs:LS_PENDING`) | `localStorage` is not durable storage; without the server nothing can be sold (ADR-0010) |
| Numbers | receipt, refund and ticket numbers come from one per-tenant counter (`tenant_counters`) | two offline tills cannot both take "the next number" |
| IDs | sales/orders/payments are UUIDs; drawers, registers and lines use integer autoincrement | an integer primary key made on a device collides with the server's |
| Bills | shared between devices with a version compare-and-swap on the server | there is no server to arbitrate while offline |
| Prices / staff | read live from the server | an offline till needs a validated, versioned local snapshot |

## Decision

### 1. Topology: one "edge node" per till, the same code base

```
 [AXAR ONE UI] --http://127.0.0.1--> [edge node: axar_pos, mode=edge]
                                          |  SQLite (WAL, synchronous=FULL, encrypted)
                                          |  local journal + sync outbox + print queue
                                          '--HTTPS, device credentials--> [central: axar_pos, mode=central, PostgreSQL]
```

- Each till runs the AXAR POS application locally in **edge mode**, bound to `127.0.0.1` only. The UI talks to it and **never directly to the central server**.
- The edge node uses the same domain services that already run on SQLite today. The central server keeps PostgreSQL + RLS.
- Edge mode adds four pieces: the sync agent, the local snapshot, the device journal and the print queue.
- The browser `localStorage` journal is no longer a source of truth. It is kept only as a UI crash guard and is removed once the edge node has run in a pilot.
- **Installable shell.** The owner rules out PWA-only.
  - Desktop tills (Windows/Linux): Tauri shell, with the edge node bundled as a sidecar process (PyInstaller).
  - Android: later. It needs a separate validation spike (Python on Android vs a Kotlin port of the sync agent) before any commitment.
  - Packaging is its own stage (O5); stages O1–O4 run the edge node as a plain local process, which is enough for every test.

### 2. Durable local storage

| Requirement | How | Test |
| --- | --- | --- |
| A committed local transaction survives power loss (RPO 0, as long as the storage itself is intact) | SQLite `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`; the business rows, the sync outbox row and the print job are written in **one** transaction **before** success is shown | kill -9 at random points while 100+ sales run; restart; every sale that was shown as done is present; none is half-written |
| Corruption | `PRAGMA quick_check` at start; on failure the till does not sell. It shows "repair", keeps the file and restores from the last acked state + a local encrypted backup | corrupted-file test |
| Low disk | free-space check before each money operation (configurable floor, e.g. 200 MB); below it, new sales are refused with a clear message | fake-fs test |
| Encryption at rest | SQLCipher (or an OS-encrypted app-data volume where SQLCipher is not available); key in the OS keychain; no card data is ever stored | key-missing test → refuses to open |
| Retention | nothing is deleted before the server acks it durably; after the ack, rows stay for audit for N days (configurable), then archive | unit test on purge |

### 3. Identity of offline records

- **Every syncable record gets a globally unique id made on the device:** ULID text, kept in the existing `String(36)` columns.
  - Integer primary keys stay internal to each database.
  - Synced references use the ULID / `public_id` columns.
  - Migration path: G-OFF-3 below.
- Every journal entry carries:
  - `device_id`;
  - `device_seq` — strictly increasing per device, with no gaps;
  - `prev_hash` and `hash` — a SHA-256 chain per device, which gives tamper evidence;
  - `local_time` (wall clock) and `monotonic_ms`;
  - `snapshot_version` — the prices/tax/staff snapshot the entry was decided on;
  - `correlation_id`;
  - the actor (staff id plus how the staff member authenticated: online or offline PIN).
- **Document numbers are per register.** Each register has its own series, e.g. `K1-000123`.
  - A series is unique and gap-free per register and needs no server. Today's per-tenant series becomes per register (migration contract: ADR-0013).
  - Customer-invoice reference numbers (*viitenumber*) are built from `register number + per-register sequence`. They stay unique offline and still carry the 7-3-1 check digit.

### 4. What a till may do offline

| Operation | Offline? | Rule |
| --- | --- | --- |
| Cash sale, cash split | **yes** | after the durable local commit; printed receipt says nothing different (receipt number is valid) |
| Card | **no** (default) | disabled with an explanation unless the configured terminal/provider adapter declares certified offline support (`capabilities.offline_purchase`, #25). An `unknown` card answer is never charged again without a status query |
| Bank transfer (invoice) | issue: **yes**, as `pending` | reference number from the register series; nothing becomes paid until the bank line is matched online (as today) |
| Table bills | **only bills this device holds a lease on** | §6 |
| Kitchen ticket | **yes** | local print queue, same transaction as the order change; KDS updates on reconnect |
| Cash refund | **limited** | only for a sale made on **this** device, with a manager PIN that is valid offline, capped per shift (configurable); otherwise waits for online |
| Drawer open/close | **yes** | close computes from the local ledger; the server re-checks on sync and opens a reconciliation exception on a mismatch |
| Price change, staff admin, recipe edit, goods receipt | **no** | back-office operations are online only |
| Stock | consumption is recorded as an **intent** | offline stock is an estimate; the authoritative movement is written by the central server on ingestion (as `stock.consume` does today); oversell is allowed and flagged (existing negative-stock policy) |

### 5. Snapshot (pull) and offline validity

- **Snapshot contents:** catalog, prices and VAT rates with effective dates, units of measure, the recipe/stock policy needed at the till, and tables. It also carries:
  - staff permitted at this outlet with **argon2 PIN hashes** and roles (lockout counters kept locally);
  - printer routing, the register's drawer state, and bills leased to this device;
  - the device's offline grant.
- The snapshot is **versioned, signed by the server** (Ed25519 or equivalent) and validated before use. Changes arrive as deltas on a cursor; a full snapshot is the fallback.
- **Offline grant:** a signed `{device_id, outlet_id, not_after, max_cash_sales, max_refund_cents}`.
  - Past `not_after` (default 72 h, configurable), the till stops selling and says why. This is the "maximum offline period".
  - Revoking a device or a staff member takes effect at the next contact. This is a residual risk inside the grant window, documented and bounded by its length.
- **Prices:** a sale uses the snapshot price that is valid at the sale's local time.
  - If the snapshot is older than the grant allows, the till refuses.
  - If the server later finds the price had changed during the offline period, the sale is **accepted as sold** (it was the legal price shown to the customer) and is **flagged** in the exception queue. No silent repricing.
- **Clock:**
  - Every sync records server time minus device time.
  - A skew above a threshold (e.g. 5 min) is shown to staff and recorded on each entry.
  - Business date comes from the device clock corrected by the last known skew.

### 6. Bills shared between devices: lease, not last-write-wins

- An open bill is **leased** to one device: `bill_leases(order_id, device_id, epoch, expires_at)`.
  - Only the lease holder may change it.
  - Online, the lease moves on request ("take over table"): the epoch increments and the old holder's later changes are rejected.
- Offline, a device can change only bills it holds. It can open new bills on free tables, but they are **local-only** until sync.
  - If two devices opened the same free table offline, both bills are kept on sync and flagged for merge. No data is dropped.
- Edits made under a stale lease epoch arrive with that epoch; the server **quarantines** them for a manager instead of applying them.

### 7. Sync protocol (push)

```
POST /api/v1/sync/push   (device credentials + session-independent device signature)
{ device_id, batch: [ {device_seq, entry_id(ULID), type, version, payload, snapshot_version, prev_hash, hash, local_time, ...} ] }
→ { acked_through: <highest contiguous seq>, results: [ {device_seq, status: accepted|duplicate|quarantined, reason?} ] }
```

- Batches go in strict `device_seq` order. The server keeps `device_inbox(device_id, device_seq)` unique plus `device_cursor(device_id, last_seq, last_hash)`.
- Each entry is applied through **the same domain service**, in its own transaction, with the inbox row. The server checks:
  - the hash chain;
  - that the device and the staff member were valid at the entry's time, within the grant;
  - tenant, entity and outlet scope;
  - snapshot validity;
  - the domain invariants.
- **Idempotent:** a re-sent entry or batch gives `duplicate` and changes nothing.
- **Partial failure:** entries up to the first failure are acked. An entry that fails *validation* (not a transient error) is moved to `sync_quarantine` with the reason, acked as `quarantined` so later entries are not stuck, and shown in the HQ repair queue.
  - Sales are never rejected outright: the money was taken.
  - A sale that breaks a rule (expired grant, revoked staff, unknown product) is ingested **as recorded** and raised as an exception.
- Transient errors are retried with exponential backoff and jitter. The cursor is resumable after any crash.
- **Pull:** `GET /api/v1/sync/pull?cursor=` returns config deltas and operational messages authorized for this device's tenant/entity/site/outlet only.
- Both directions carry a `schema_version`. The server accepts the last two edge versions; updates are staged per device.

### 8. Ordering, consistency and invariants

| Boundary | Consistency |
| --- | --- |
| One device's journal | strict order, hash-chained, gap-free |
| A bill | linearizable through the lease holder |
| Receipt / refund numbers | unique and gap-free per register (no global order) |
| Stock | eventually consistent; authoritative on the server; oversell flagged, never blocks a sale |
| Cash drawer | per register; the local close is re-verified by the server |
| Accounting export | only from server-ingested events (outbox → `books.export`), never from a device |

**Server-side test after the acceptance demo:** count and sum of all device entries = central sales/refunds/payments; no duplicates; every entry is either ingested or quarantined with a reason.

### 9. Observability

- Each device sends a heartbeat on every sync: release version, `last_sync_at`, pending entry count, oldest pending age, quarantine count, free disk, database check result, clock skew.
- HQ has an "Seadmed" dashboard; the till shows a banner when offline (time since last sync and pending count) and when nearing grant expiry.

### 10. Security

- TLS. Device credentials come from enrollment (exists today); in addition each device has a **signing key** in the OS keychain for push batches.
- Snapshots are signed by the server.
- Offline PINs are argon2-hashed in the encrypted database, with the same lockout rules.
- No card data, ever. Key rotation goes through the pull channel.
- An emergency disable of a device or staff member applies on next contact; the grant length is the bound.

## Delivery stages (each one a draft PR with tests)

| Stage | Content | Acceptance |
| --- | --- | --- |
| **O1** | edge mode of the app; device journal (ULID, `device_seq`, hash chain) written in the same transaction as cash sales; per-register numbering; `POST /sync/push` with inbox, ack, duplicate, quarantine; sync agent with backoff | 2 edge nodes (SQLite) + central (PostgreSQL): offline 100+ cash sales each, kill -9 mid-sale, restart, reconnect → exact counts and sums once; duplicate batch replay → no change |
| **O2** | signed snapshot + delta pull; offline staff PIN; offline grant + expiry; price-validity rule; clock skew | revoked staff, expired grant and price change while offline become flagged exceptions; the till refuses after expiry |
| **O3** | bill leases, offline bills, kitchen print queue, offline cash refund policy, offline drawer close + server re-check | 2 tills, same outlet: lease takeover; stale-epoch edit quarantined; drawer close reconciled |
| **O4** | HQ device dashboard, heartbeat, banner; quarantine repair UI | |
| **O5** | installable shell (Tauri + sidecar), SQLCipher/keychain, local encrypted backup/restore, update-mid-sale, reinstall | hardware-in-the-loop on the pilot till |
| **O6** | owner's full acceptance demo (#29): 2 outlets, 2 registers, 100+ offline sales with kitchen and receipt jobs, reboot mid-transaction, then reconcile | report with commit SHA and exact counts |

## Owner decisions (2026-11-25)

| Question | Decision |
| --- | --- |
| Maximum offline period | **72 h** (default) |
| Till platforms | **Windows and Android**. O5 delivers Windows first (Tauri shell + sidecar edge node); Android needs a validation spike before any commitment: Python edge on Android vs a native port of the sync agent |
| Per-register receipt numbering (`K1-000123`) | **accepted** |
| Offline cash-refund cap | owner had no preference → default **€50 per shift**; this-device sales only, manager PIN |
| Who sets the limits | **the owner, in the POS settings screen**: the maximum offline period, the refund cap, the number prefix/format per register and the start number. Settings are server-side, versioned and audited; changes reach the tills through the signed snapshot; only the manager/owner role may change them |

Still open: real legal entities and zoo outlet names (ADR-0013).
