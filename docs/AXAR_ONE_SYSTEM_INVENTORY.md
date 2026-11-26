# AXAR ONE — code-backed system inventory (#60)

**Audited state:** `main` @ `1b7a3af` (2026-11-26), plus open PRs #8, #9, #28, #30, #37, #45, #51 and every issue listed in #60.
**Method:** each row cites the code, the tests and the merge commit. Issue text, a merged PR or green CI alone is never counted as "works". A row says **E2E TESTED** only when an automated test drives the whole flow through the HTTP API or the browser.
**Successor of:** the 2026-11-25 readiness audit (PR #45, `main` @ `642e83e`). Since then offline O1a–O3, backups, security gates, the terminal adapter and C3 payments were merged.

---

## Kokkuvõte eesti keeles (omanikule)

**1. Mis päriselt töötab** (koodis, automaattestidega, `main`-is):
- **Kassa:**
  - sularahamüük, kassasahtli avamine ja sulgemine;
  - osalised tagastused juhi kinnitusega;
  - tšekinumbrid kassa kaupa (K1-000123);
  - päevaaruanne.
- **Lauatellimus:** laud → köök → valmis → arve.
  - Arve saab jagada sularaha, **simuleeritud** kaardi ja pangaülekande vahel.
  - Ülekande puhul tekib arve viitenumbriga, mis seotakse pangaväljavõtte reaga.
- **Ladu ja omahind:**
  - saateleht → laoseis → retsept → müük → laost mahakandmine → kaubakulu → marginaal;
  - laoseisu kooskõlakontroll.
- **Sisselogimine ja õigused:**
  - seadmete registreerimine;
  - PIN-kood, juhi parool + TOTP;
  - rollid;
  - firmade andmete eraldus PostgreSQL-is.
- **Võrguühenduseta kassa:**
  - müük ja tagastus kuni 72 h ilma keskserverita;
  - hiljem sünkroonimine täpselt üks kord; vigased kirjed lähevad juhi läbivaatusele;
  - kassa lukustub piiri ületamisel.
- **Sündmuste ahel:** iga müük → sündmus → lao mahakandmine ja raamatupidamise ekspordijärjekord, täpselt üks kord.
- **Varundus ja taastamine:** automaatne taastamisharjutus CI-s.
- **UUS selles PR-is:**
  - **registrite kooskõlakontroll** — 13 reeglit üle kassasahtli, müügi, maksete, tagastuste, tšekinumbrite, sündmuste, lao, köögi, arvete ja kassade sünkroonimise;
  - **müügi jälgimine** läbi kõigi registrite;
  - täispäeva E2E-test, mis tõestab, et kõik registrid on kooskõlas;
  - tahtliku rikkumise testid, mis näitavad, et kontroll leiab iga rikkumise ja nimetab selle.

**2. Mis on poolik:**
- **Kaardimakse on simulaator.** Päris terminali pole. Pakkujaneutraalne terminaliadapter on olemas, aga **makseahelaga ühendamata**.
- **Raamatupidamise eksport:** read jäävad järjekorda, sest **edastajat pole**. AXAR CONNECT on PR-is #8 ega ole `main`-is.
- **Võrguühenduseta kassa:**
  - tagastused sünkroonitakse;
  - **lauaarved** (jagatud/kaart/ülekanne) lähevad käsitsi läbivaatusele;
  - Windowsi/Androidi paigaldus ja HTTP-transport puuduvad.
- **Tagastus:**
  - ainult sularahas;
  - kaupa lattu tagasi ei kanta;
  - mahakandmise valikut pole (#48).
- **Mitme kassa ja asukoha toimimine:** asutus → asukoht → kassa/ladu on olemas; juriidiline isik → objekt → müügikoht puudub (#27).
- **Tšekk:** ainult tekstina; printerit pole.

**3. Mis puudub:**
- toodete ja menüü loomine kontoris (POS-i nupud tulevad ainult demoandmetest);
- menüü avaldamine ja telerimenüü;
- külalise QR-tellimus;
- printerid ja päris köögiekraani riistvara;
- PEOPLE / HACCP;
- finantsjuhtpaneel;
- mitme firma hierarhia;
- keskserveri majutus ja monitooring;
- NERVE tervisepaneel;
- laienduste platvorm;
- inventuur (laoloendus), praak, ladudevaheline liikumine, partii/säilivusaeg.

**4. Mis on omavahel ühendamata:**
- terminaliadapter ↔ maksed;
- raamatupidamise järjekord ↔ raamatupidamisprogramm;
- tagastus ↔ ladu;
- toode ↔ menüü avaldamine;
- **kassa seadetel, karantiini järjekorral, pangaimpordil, töötajatel/seadmetel ja kooskõlakontrollil on API, aga kasutajaliidest pole.**

**5. Järgmine PR ja mõõdetav valmimiskriteerium:** vaata `docs/DELIVERY_ROADMAP.md` §1.

**Ühtki moodulit ei ole päris seadmel testitud ega tootmiskõlbulikuks tunnistatud. Kõik maksed on simuleeritud.**

---

## 1. Status scale

| Code | Meaning (#60) |
| --- | --- |
| PLANNED | in an issue or ADR only |
| MISSING | not planned in detail and not in code |
| PARTIAL | some code in `main`, but the requirement is not met |
| IN CODE | in `main` with unit/API tests |
| INTEGRATED | connected to the neighbouring modules in `main` (events / shared registers) and tested across the boundary |
| E2E TESTED | the whole user flow is driven by an automated API or browser test |
| DEVICE TESTED | verified on real hardware (none so far) |
| PRODUCTION READY | hosted, monitored, backed up, legally reviewed, piloted (none so far) |

Payments: **SIMULATED** means no money and no real terminal or bank. Nothing in AXAR ONE moves real money.

## 2. Evidence collected for this document

| # | Command | Result |
| --- | --- | --- |
| V1 | `pytest tests` (SQLite), this branch | 355 passed, PostgreSQL-only tests skipped |
| V2 | `AXAR_TEST_PG=… pytest tests` (PostgreSQL 16, RLS role `axar_app`) | 374 passed |
| V3 | `pytest tests/test_e2e_chain_reconciliation.py` | 16 passed (1 business day, 13 invariants, 11 tamper cases, R13 sync, dead letter) |
| V4 | mutation check of `axar_pos/reconcile.py` (6 deliberate bugs) | 6 / 6 caught |
| V5 | GitHub: PR list, issue list (#1–#60) | §5 |
| V6 | `main` CI for `1b7a3af` (axar-pos + security) | success |
| V7 | Vercel deployments for `1b7a3af` | CANCELED by the Ignored Build Step; production unchanged (`dpl_5hHC…`, `8de19c3`) |
| V8 | route list of `axar_pos/api.py` and API calls made by `static/one/*.mjs` | §4 "API only" column |

## 3. Capability matrix

Abbreviations:
- `S` = `products/axar-pos/axar_pos/`
- `T` = `products/axar-pos/tests/`
- `E` = `products/axar-pos/tests_e2e/`

| # | Capability | Issue | Status | Code | Tests | Merged in | Missing for the next level |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C01 | Cash sale, quote, receipts (text), idempotency | #7, #3 | **E2E TESTED** | `S/services.py` `create_cash_sale` | `T/test_sales.py` (18), `E/test_cashier_ui.py` (14) | #7 `d546a95` | printed receipt (#25), legal receipt review |
| C02 | Drawer open/close, variance + manager approval | #7 | **E2E TESTED** | `services.py` `open_drawer`, `close_drawer` | `T/test_drawer_and_reports.py` (8), e2e | #7 | Z-report sign-off by accountant |
| C03 | Cash refund (partial, manager approval, cumulative rounding) | #7, #48 | **E2E TESTED (cash only)** | `services.py` `create_cash_refund` | `T/test_refunds.py` (25), `T/test_refund_allocation.py` (6) | #7 | card/transfer refund, restock / write-off disposition, credit note (#48) |
| C04 | Invoice cancel / correct / credit note with per-line stock disposition | #48 | **PLANNED** | — | — | — | ADR + data model + UI (#48) |
| C05 | Per-register receipt series (K1-000123), hash-chained device journal | #29, #49 | **INTEGRATED** | `journal.py` | `T/test_device_journal.py` (14) | #49 `c1f541d` | journal verify UI |
| C06 | Table orders, kitchen tickets, ready status, realtime (SSE) | #13, #16 | **E2E TESTED** | `orders.py`, `api.py` `/events/stream` | `T/test_orders.py` (13), `E/test_cross_device_orders.py` (7) | #13, #21 | stations / routing, bump screen hardware, printer fallback |
| C07 | Bill payments: split cash / **simulated** card / transfer receivable, bank import matching | #15 C3 | **E2E TESTED (SIMULATED)** | `payments.py` | `T/test_payments.py` (23), `E/test_bill_payments_ui.py` (4) | #26 `0d7a21c` | real terminal; bank import UI; receivables UI |
| C08 | Provider-neutral card terminal adapter + sandbox + contract tests | #25 | **IN CODE, NOT CONNECTED** | `terminals/` | `T/test_terminal_contract.py` (20) | #52 `4786abe` | `payments.py` still calls its own mock (`MockTerminalTxn`), not `terminals/`; provider choice + sandbox access (owner) |
| C09 | Printers (receipt, kitchen), receipt designer | #25 | **MISSING** (text receipt only) | `api.py` `receipt.txt` | receipt text tests | — | print queue, ESC/POS bridge, templates, hardware |
| C10 | Identity: devices, PIN, manager password + TOTP, RBAC, step-up approvals | #2, #34 | **E2E TESTED** | `auth.py` | `T/test_auth.py` (42), `E/test_server_auth.py` (5) | #19 `7fe3a7e` | staff/device admin **UI** (API only) |
| C11 | Tenant isolation (PostgreSQL RLS, non-owner role, append-only grants) | #2, #34 | **INTEGRATED** | migrations 0001–0008 | `T/test_postgres.py` (13), `T/test_tenancy_and_identity.py` (10) | #17 `a4d0af8` | pen test |
| C12 | Transactional outbox v2, consumers, inbox dedup, dead letters, realtime | #15 C1, #42 | **INTEGRATED** | `events.py`, `consumers.py`, `worker.py` | `T/test_events.py` (20), `T/test_event_contracts.py` (7) | #21 `f614f30` | event-health **UI**, alerting, chaos tests (#42) |
| C13 | Inventory: items, warehouses, suppliers, delivery notes / invoices, returns, moving average cost | #14 | **E2E TESTED** | `inventory.py` | `T/test_inventory.py` (16), `E/test_inventory_ui.py` (2) | #23, #24 | stock count, waste, transfers, lot/expiry (#14) |
| C14 | Recipes (versioned), production batches, sale consumption, COGS, margin | #14, #38 | **INTEGRATED** | `inventory.py`, consumer `stock.consume` | `test_inventory.py`, this PR's E2E | #23 | chef workspace, allergens, recipe approval (#38) |
| C15 | Product / menu create, price change, activate → POS buttons | #40 | **MISSING** | catalog only from `db.seed_demo` / direct DB | — | — | product API + office UI (#61); menu publish, TV, button editor (#40) |
| C16 | Accounting export | #3, #4, #8 | **PARTIAL** | consumer `books.export` → `export_queue` | `test_events.py`, R8 in this PR | #21 | delivery adapter + acknowledgements (AXAR CONNECT PR #8, v1 only; v2 missing) |
| C17 | Daily report, margin report | #32 | **IN CODE** | `services.daily_summary`, `inventory` margin | `test_drawer_and_reports.py`, this PR | #7, #23 | CONTROL dashboard, accountant exports (#32) |
| C18 | Offline till: edge mode, outbox, exactly-once ingest, quarantine, reconnect | #29 | **INTEGRATED** | `sync.py`, `sync_agent.py` | `T/test_offline_sync.py` (14) incl. 72 h soak | #53 `54cd1eb` | HTTP transport, installer, SQLCipher (#29 O5) |
| C19 | Offline limit (1–72 h), clock-back guard, till banner | #29 | **E2E TESTED** | `offline.py`, `app.mjs` banner | `T/test_offline_limit.py` (8), `E/test_offline_banner.py` (4) | #57, #58 | **POS settings UI** (owner decision: limit set in the settings screen; today API only) |
| C20 | Offline refunds sync to the central copy of the sale | #29 | **INTEGRATED** | `sync._apply_refund` | `T/test_offline_refunds.py` (5) | #59 `1b7a3af` | — |
| C21 | Offline table bills (split / card / transfer) | #29 | **PARTIAL** (quarantined for review, never lost) | `payments.py` journal record | `T/test_payments_offline.py` (2) | #26 | automatic central apply |
| C22 | Windows / Android till packaging | #29 | **PLANNED** (ADR-0012, PR #30 draft) | — | — | — | O5 packaging, device test |
| C23 | Backup + restore drill | #34 | **IN CODE** (CI drill on PostgreSQL) | `backup.py` | `T/test_backup_restore.py` (5) | #47 `55cd632` | scheduled off-site backups in a hosted environment, manual drill report |
| C24 | Security gates: secret scan, SAST, dependency audit, threat model SEC-0 | #34 | **IN CODE** (CI) | `.github/workflows/security.yml` | CI | #46 `a599f32`, #50 | branch protection **applied** (owner), pen test, encryption at rest |
| C25 | Cross-register reconciliation + document trace | #60 | **E2E TESTED (this PR)** | `reconcile.py`, `/api/v1/admin/reconciliation`, `/api/v1/admin/trace/sales/{id}` | `T/test_e2e_chain_reconciliation.py` (16) | this PR | office UI; scheduled run + alert |
| C26 | Organization hierarchy (legal entity → site → outlet → register + warehouse) | #27 | **PARTIAL** (tenant → location → register / warehouse) | `models.py` | — | — | ADR-0013 (PR #30), migration + backfill |
| C27 | Guest QR / web ordering | #18 | **PLANNED** | — | — | — | all |
| C28 | Menu studio, TV boards, POS button editor | #40 | **PLANNED** | — | — | — | all |
| C29 | Chef workspace, optimizer | #38, #39 | **PLANNED** (recipe editor with live cost exists: C14) | `static/one/inventory.mjs` | `E/test_inventory_ui.py` | #24 | — |
| C30 | PEOPLE + SAFE (contracts, e-signature, HACCP) | #33 | **PLANNED** | — | — | — | all |
| C31 | CONTROL dashboard, analytics | #32 | **PLANNED** | — | — | — | all |
| C32 | Extensibility (actions, modules, APIs) | #31 | **PLANNED** | — | — | — | all |
| C33 | NERVE health dashboard, chaos tests | #42 | **PARTIAL** (`/admin/event-health` JSON; kill-9 and crash tests) | `api.py` | `E/test_durability.py`, `E/test_crash_recovery*.py` | #46 | UI, alerting, power-cut test |
| C34 | Central server hosting, TLS, monitoring, incident process | #44 | **MISSING** | — | — | — | #62 |
| C35 | Website (Next.js, separate product) | — | live on Vercel | `src/` | — | — | PR #51 (critical `next` fix) waits for the owner; a separate website data-exposure finding was reported to the owner directly, not in this public document |

**DEVICE TESTED:** none. **PRODUCTION READY:** none.

## 4. API without a user interface

The till/office UI (`static/one/*.mjs`) calls the routes below. The following routes exist only as an API: an operator cannot use them without a developer.

| Route group | Used by UI | Consequence |
| --- | --- | --- |
| `/api/v1/admin/pos-settings` (offline limit) | no | owner decision "limit set in the POS settings screen" is **not met in the UI** |
| `/api/v1/sync/quarantine` (+ retry / dismiss) | no | a manager cannot review offline entries without a tool |
| `/api/v1/receivables/*` (invoices, bank import) | no | bank matching is API-only |
| `/api/v1/payments/{id}/reconcile` (unknown card result) | yes (bill screen) | — |
| `/api/v1/admin/staff`, `/devices`, `/enrollment-codes` | no | staff and devices are set up through the API |
| `/api/v1/admin/registers/{id}/receipt-series`, `/journal/verify` | no | — |
| `/api/v1/admin/event-health`, `/admin/auth-events` | no | no health screen (NERVE) |
| `/api/v1/admin/reconciliation`, `/admin/trace/sales/{id}` (this PR) | no | next UI step (roadmap P1-3) |
| `/api/v1/inventory/production`, `/returns`, `/margin`, `/reconciliation` | partly | — |

## 5. Pull requests and issues

**Open PRs:**

| PR | Content | State | Blocker |
| --- | --- | --- | --- |
| #51 | website `next` 16.2.4 → 16.4.6 (critical) | draft | owner approval for production deploy |
| #8 | AXAR CONNECT accounting previews (AION) | draft, v1 only | `pos.sale.completed` v2 adapter; retarget to `main` |
| #9 | build workflow + scheduled agent action with write permissions | draft | branch protection + owner decision |
| #28, #30, #37 | operating model, ADR-0012/0013, living-system roadmap (docs) | draft | owner review; #30 is the source for #27 |
| #45 | readiness audit 2026-11-25 | draft | superseded by this document |

**Issues named in #60:**

| Issue | Covered by rows | Summary state |
| --- | --- | --- |
| #2 auth / isolation | C10, C11 | done in code; UI for staff/devices missing |
| #3 ledger correctness | C01–C03, C16, C25 | invariants enforced and now checked; real ledger posting missing |
| #4 accounting / bank adapters | C07, C16 | bank import (file lines) yes; accounting adapter no |
| #10, #11 cashier UI + API | C01, C02, C06 | E2E tested; no usability study with cashiers |
| #14 inventory | C13, C14 | core done; count / waste / transfer / lot missing |
| #15 core coordination | C12, C25 | events + reconciliation; durable workflows partial |
| #16 food service modes | C06 | table + express; other modes not specified in code |
| #18 guest | C27 | planned |
| #25 terminal / printer | C08, C09 | adapter not connected; no printer |
| #27 hierarchy | C26 | partial |
| #29 offline-first | C18–C22 | sync done; packaging missing |
| #31 extensibility | C32 | planned |
| #32 control | C17, C31 | reports yes; dashboard no |
| #33 people / safe | C30 | planned |
| #34 security | C11, C23, C24 | gates and drill in CI; settings and pen test owner-side |
| #35 master plan | all | — |
| #38, #39 chef | C14, C29 | partial |
| #40 menu studio | C15, C28 | missing; blocks the golden path |
| #41 experience | UI rows | contrast issues found 2026-11-25 (axe) still open |
| #42 nerve | C12, C33 | partial |
| #44 readiness audit | this document | — |
| #48 cancel / credit | C03, C04 | planned |

## 6. Requirements without an issue (created with this PR)

| Requirement | Why it needs its own issue | Issue |
| --- | --- | --- |
| Product & menu item management (create / price / activate → POS button) | the golden path is broken here; #40 is a large studio, this is the minimal pilot blocker | #61 |
| Central server hosting, TLS, monitoring, alerting, incident runbook | pilot blocker B2 of #44, no dedicated issue | #62 |

Created as #61 and #62.
