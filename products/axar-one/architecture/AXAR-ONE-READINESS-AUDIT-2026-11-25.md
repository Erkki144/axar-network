# AXAR ONE — independent readiness audit (2026-11-25)

Assignment: #44. Auditor: Claude (AXAR BUILD). Audited state: **`main` @ `642e83e`**, plus every open PR and branch listed in §3.

**Rules this document follows:**
- A result appears here only if a command was **actually run** in this audit; the commands are in §2.
- Issue text is never counted as implementation.
- The status scale is the one #44 asks for:
  1. IDEA
  2. ADR PROPOSED
  3. CODE IN PR
  4. CODE IN MAIN
  5. TESTED
  6. INTEGRATED
  7. HARDWARE VERIFIED
  8. PRODUCTION READY

  A module's status is the **highest level for which there is evidence**.

---

## 1. Executive summary

**Built and evidenced:**
- `main` now contains the AXAR POS server-centric core. It covers:
  - cash sales, drawers, refunds and table bills;
  - kitchen tickets;
  - server authentication (devices, PIN, manager MFA, RBAC, PostgreSQL RLS);
  - an event outbox with consumers and realtime sync;
  - inventory and costing with recipes;
  - the AXAR ONE touch UI.
- Every test suite passes on `main`: 248 SQLite / 262 PostgreSQL / 44 browser.
- Migrations upgrade, downgrade and upgrade again without drift.
- The Python dependencies have no known vulnerabilities.
- The stack was merged today on the owner's chat approval. Vercel's Ignored Build Step skipped all 13 production builds; the live website is unchanged (§4).

**Not built:**
- Of the 2027 zoo scope, **offline-first operation** (#29) is not implemented; ADR-0012 is only proposed.
- **No payment terminal, printer, TV, QR/guest, menu publishing, chef, PEOPLE/SAFE, CONTROL dashboard, backup/DR or organization hierarchy code** exists in `main`.
- **There is no production environment for the POS central server.**

**AXAR ONE is not production-ready and not pilot-ready.** It is a tested core.

**Highest risks found (P0):**

| ID | Risk |
| --- | --- |
| R-01 | Offline-first is a hard requirement, and there is none |
| R-02 | Repository is **public** and `main` is **not protected** (no required reviews or checks) |
| R-03 | The **live website** has **13 npm vulnerabilities, 1 critical** (`next` 16.2.4); this is outside AXAR ONE but in the same repo and deployment |
| R-04 | No backup/restore and no DR drill |
| R-05 | No payment terminal or printer integration, and no hardware has been verified |
| R-06 | No hosting, monitoring or incident process for the central server |

---

## 2. Method and evidence (commands actually run)

| # | Command / procedure | Result |
| --- | --- | --- |
| E1 | `git merge-base --is-ancestor` along the whole chain; `git merge-tree` of every open branch against `main` | chain linear; **all open branches merge clean** into `main` @ `642e83e` |
| E2 | `pytest tests` (SQLite), worktree at `main` @ `642e83e`, Python 3.13, `requirements*.txt` pins | **248 passed, 14 skipped** (the skipped ones are PostgreSQL-only) |
| E3 | `AXAR_TEST_PG=… pytest tests` against PostgreSQL 16, as the non-owner role `axar_app` with RLS | **262 passed** |
| E4 | `alembic upgrade head && alembic downgrade base && alembic upgrade head && alembic check` on an empty PostgreSQL | OK, "No new upgrade operations detected" (no model/migration drift) |
| E5 | `pytest tests_e2e` (Playwright Chromium against a real local uvicorn) | **44 passed** |
| E6 | GitHub Actions `axar-pos` push runs on `main` for the merges #21, #22, #23, #24, #36 (the five most recent of 11 push runs) | all **success** (run 45607881412 for `642e83e`); the older 6 were not read |
| E7 | `pip-audit -r requirements.txt -r requirements-dev.txt -r requirements-e2e.txt` | "No known vulnerabilities found" (before #36: **28** findings, fixed there) |
| E8 | `bandit -r axar_pos` (excluding migrations) | 1 LOW (B311, `random` used for retry jitter): false positive |
| E9 | Secret-pattern scan of tracked files **and of the full git history of all refs** (Stripe live/test, AWS, private keys, Anthropic, GitHub, Resend, `whsec_`) | 0 matches |
| E10 | `git ls-files` for sensitive file types | `data/axar.db` is tracked (website). Inspected: tables `agent_logs`, `jobs`, `sqlite_sequence`, **0 rows**. Schema only, no personal data |
| E11 | `npm audit --package-lock-only --omit=dev` (website at repo root) | **13 vulnerabilities: 1 critical (`next`), 7 high, 5 moderate**; all have fixes available |
| E12 | GitHub API (read-only): repository and `main` branch | `visibility: public`, `main.protected: false` |
| E13 | Vercel API (read-only): production deployments of project `axar-network` | 13 production deployments for the merges, **all CANCELED by the Ignored Build Step**; the latest READY production is `8de19c3` |
| E14 | Vercel preview verification before #43 merged (products-only / docs-only / vercel.json-only / `--no-ff` merge commit / `src/` change) | skip / skip / skip / skip / **build READY** |
| E15 | AXAR CONNECT (PR #8) own suite: `pytest products/axar-connect/tests` | **67 passed** |
| E16 | Cross-module test: real POS outbox events from the integrated code → CONNECT `from_pos_outbox_v1` / `from_pos_refund_outbox_v1` + journal previews | 44 sales + 22 refunds → **66/66 accepted, 0 errors** (v1 only) |
| E17 | PR #26 (C3 payments) merged onto the integration head; full suites | SQLite 270 / PostgreSQL 284 / migrations OK / browser 48 passed |
| E18 | **kill -9 of the server** during 6 concurrent cash-sale clients (SQLite), then restart on the same file; every key retried | 145 sent, 135 acknowledged (201) before the kill, **0 of 135 missing** after restart; the 10 unknown-outcome keys were recorded exactly once on retry; total 145 = 145; receipt numbers gap-free. **Process kill only, not an OS power cut** |
| E19 | UX measurement script (Playwright, 390×844 and 1024×768, local loopback) | §8 |
| E20 | axe-core 4.10.3, WCAG 2 A + AA, on the express, tables and kitchen views | **color-contrast (serious)** on 27 / 35 / 3 nodes; no other violations |

The scripts used for E16, E18, E19 and E20 were run from the session scratchpad. Their logic is described above; they are not committed. Committing them as CI tests is in the plan (§11).

---

## 3. Integration and dependency state

**Merged into `main` today (owner chat approval):**

| Order | PR | Merge commit | Content |
| --- | --- | --- | --- |
| 1 | #43 | `91d1007` | Vercel Ignored Build Step |
| 2 | #6 | `394dd96` | Sprint 0 docs |
| 3 | #7 | `d546a95` | POS slices 1–3 (cash, drawers, refunds) |
| 4 | #12 | `ecd8f62` | AXAR ONE UI wired to the API |
| 5 | #13 | `953f340` | table bills, kitchen |
| 6 | #17 | `a4d0af8` | PostgreSQL, Alembic, RLS |
| 7 | #19 | `7fe3a7e` | identity, RBAC, MFA |
| 8 | #20 | `23d8c64` | architecture baseline |
| 9 | #21 | `f614f30` | outbox v2, consumers, SSE |
| 10 | #22 | `2d70baf` | UX pass |
| 11 | #23 | `0620050` | inventory and costing |
| 12 | #24 | `b4b4daa` | inventory screens |
| 13 | #36 | `642e83e` | dependency security upgrade + Starlette 1.x fix + CI dependency audit |

**Still open:**

| PR | Author / branch | Status | Merge with `main` | Blocker |
| --- | --- | --- | --- | --- |
| #26 | Claude, C3 payments | draft, base `main` | clean | CONNECT does not accept `pos.sale.completed` **v2** (AION) |
| #30 | Claude, ADR-0012/0013 | draft, base = #26 branch | clean | owner review; rebase onto `main` after #26 |
| #8 | AION, AXAR CONNECT | draft, base `feature/axar-pos-sprint-0` (now merged) | clean | needs retargeting to `main`, a v2 adapter, and adds its own workflow `axar-connect.yml` |
| #9 | AION, build workflow + UX prototype | draft | clean | adds `axar-agent-dispatch.yml`, which runs a **scheduled** Claude action with `contents: write`, `pull-requests: write` and `issues: write` and `secrets.ANTHROPIC_API_KEY`. Needs branch protection and an owner decision before merging (§6) |
| #28 | owner, operating model v1 docs | draft | clean | v1.1 delta in #30 `09-…` |
| #37 | owner, living-system roadmap docs | draft | clean | — |

- **Duplicate changes:** none found. Each branch touches distinct paths.
- **Unmerged migrations:** `0005_payments_c3` exists only in #26. `main`'s head migration is `0004`, so there is no fork in the migration history.
- **Stale branches:**
  - `claude/vercel-verify-{products,docs,web,merge}-0r9ip8` are temporary verification branches. The audit environment cannot delete branches (HTTP 403); the owner should delete them.
  - The merged stack branches can be deleted after the owner confirms.

---

## 4. Vercel / production deployment risk

| Question | Evidence | Answer |
| --- | --- | --- |
| Does a push to `main` deploy production? | E13: every `main` commit created a deployment with `target: production` | **yes**, automatically |
| Does the Ignored Build Step work? | E14 (previews), E13 (13 real `main` merges) | yes for commits changing only `products/`, `.github/`, `docs/` or `vercel.json` |
| Was the live site changed today? | E13: the latest READY production is still `8de19c3` | **no** |
| Residual risk | a website change merged together with AXAR changes builds as normal (intended); a `vercel.json`-only change needs a manual redeploy; the rule depends on Vercel keeping `HEAD^` in its clone (if not, `git diff` fails, and the build runs: fail-safe) | low |
| Long term | separate the deploy pipelines: the website as its own Vercel project or root directory; the POS with its own release pipeline (installable edge app, ADR-0012) | open |

---

## 5. Module status matrix

Legend: ✅ evidence exists · — none.

| Module (issue) | Highest status | Evidence / where | What is missing for the next level |
| --- | --- | --- | --- |
| **POS core** (#2, #3, #7) | **TESTED** (CI on `main`) | `products/axar-pos/axar_pos/services.py`, `orders.py`; `tests/test_sales.py`, `test_refunds.py`, `test_registers_and_concurrency.py`, `test_drawer_and_reports.py`; E2–E6, E18 | hosting, legal receipt review, hardware |
| **AXAR ONE cashier UI** (#10, #11, #41) | **TESTED** (browser) | `axar_pos/static/one/*`; `tests_e2e/*` (44); E19, E20 | WCAG contrast fixes (E20); installable app (ADR-0012); usability with real cashiers (none done) |
| **CORE events / NERVE** (#15, #42) | **TESTED** | `events.py`, `consumers.py`, `worker.py`; `tests/test_events.py` (20), `test_event_contracts.py`; SSE `/api/v1/events/stream` | NERVE scope (edge sync, device adapters, chaos tests) is IDEA; edge sync is ADR PROPOSED (#30) |
| **CONNECT accounting** (#4, #8) | **CODE IN PR** + TESTED in PR + cross-module v1 harness | PR #8; E15, E16 | v2 adapter, merge into `main`, real accounting export (none), accountant validation |
| **AUTH / SECURITY** (#2, #34) | **TESTED** | `auth.py`; `tests/test_auth.py` (38), `test_tenancy_and_identity.py`, `test_postgres.py` (RLS, grants, append-only); E7–E9 | threat model (SEC-0), branch protection, backups, encryption-at-rest config, secret scanning/SAST in CI, pen test |
| **OFFLINE / EDGE** (#29) | **ADR PROPOSED** | ADR-0012 in PR #30. `main` only has a browser crash journal (`app.mjs` `LS_PENDING`) that **refuses** to sell offline (`test_offline_is_visible_and_nothing_is_faked`) | everything in O1–O6 |
| **PAYMENTS** (#15 C3, #25) | cash: **TESTED** (simulated). Split / card / transfer: **CODE IN PR** #26, tested (E17). Terminal: **IDEA** (+ provider research in #25) | `payments.py` (PR #26) | v2 consumer, terminal adapters (Revolut / Nexi sandbox), certification, hardware |
| **PRINTER / receipt designer** (#25) | **IDEA** | only `GET /api/v1/sales/{id}/receipt.txt` (plain text) in `main` | print queue, local bridge, ESC/POS, templates, hardware |
| **INVENTORY / COSTING** (#14) | **TESTED** | `inventory.py`; `tests/test_inventory.py` (16); `tests_e2e/test_inventory_ui.py` | lot/expiry, transfers, stocktake, waste, RECEIVE photo intake |
| **CHEF** (#38) | **IDEA**; recipe editor with live cost is **TESTED** in `main` | `static/one/inventory.mjs`, `test_inventory_ui.py` | chef workspace, recipe approval, allergens |
| **CHEF OPTIMIZER** (#39) | **IDEA** | — | — |
| **MENU STUDIO / TV** (#40) | **IDEA** | no product/menu create API exists: POS buttons come from the seeded catalog only (`db.seed_demo`) | menu model, publish, TV board, button editor |
| **KDS** (#16) | **TESTED** | `orders.send_to_kitchen`, `mark_ready`; `test_orders.py`, `test_cross_device_orders.py` (incl. realtime) | stations / routing, bump states, printer fallback |
| **GUEST / QR** (#18) | **IDEA** | — | all |
| **CONTROL / FINANCE** (#32) | basic daily report and margin endpoints **TESTED**; dashboard **IDEA** | `services.daily_summary`, `/api/v1/inventory/margin`, manager view | metric dictionary, read models, exports, accountant approval |
| **PEOPLE / SAFE** (#33) | **IDEA** | — | all |
| **Organization hierarchy** (#27) | **ADR PROPOSED** (ADR-0013, PR #30); `main` has tenant → location → register/warehouse | `models.py` `Location`, `Register`, `Warehouse` | migrations 0006/0007, backfill, snapshot columns |
| **Device operations** | enrollment, revoke, register binding **TESTED** | `auth.py` devices; `test_auth.py` | heartbeat, fleet view, remote disable via the offline grant (ADR-0012) |
| **Backup / DR** | **none** | no backup scripts, no restore test (searched the repo) | everything (SEC-2) |
| **Extensibility** (#31) | **IDEA** | — | action registry ADR |
| **BUILD workflow** (#5) | **CODE IN PR** #9 | `products/axar-build/*`, workflows | governance (see #9 row in §3) |

**No module is HARDWARE VERIFIED or PRODUCTION READY.** INTEGRATED applies only to the POS ↔ inventory ↔ events chain inside `main`. The CONNECT integration is test-harness only.

---

## 6. Security P0 review (#34)

| Area | Finding | Evidence | Severity |
| --- | --- | --- | --- |
| Repository exposure | **public** repository (decision #1 open); architecture, threat surface and code are public | E12 | **P0** (owner decision) |
| Branch protection | `main` is **not protected**: no required reviews or status checks. Any write-token holder or workflow can push to production's branch | E12 | **P0** |
| Secrets | none in tree or history; the website reads its secrets from env (Stripe, Resend, Anthropic, Brave, DB) | E9 | ok |
| Tracked database file | `data/axar.db` (website): schema only, 0 rows | E10 | P3 (move out of git) |
| Python dependencies | 0 known vulnerabilities; CI job `dependency-audit` added (#36) | E7, E6 | ok |
| Website dependencies (live production) | 13 vulnerabilities, **critical `next`**; all fixable | E11 | **P0** (separate, owner-approved website PR; affects production) |
| SAST | bandit clean except one false positive; **not in CI** | E8 | P2 |
| Secret scanning in CI | **not configured** | — | P1 |
| Authentication | PIN argon2id with row-locked lockout; manager password + TOTP (replay rejected); step-up approvals bound to the request; sessions stored hashed (sha256); device tokens stored hashed | `test_auth.py`; `auth.token_hash` | ok (tested) |
| Tenant isolation | RLS with a non-owner `axar_app` role, fail-closed when no tenant is set; negative tests across tenants | `test_postgres.py`, `test_tenancy_and_identity.py` | ok (tested) |
| Demo identity headers | off by default; the `pilot` profile refuses them and requires a secret key | `config.py` `pilot_problems`; test in `test_auth.py` | ok |
| Payment data | no card data model; card is a mock only (PR #26) | `payments.py` (PR) | ok by design |
| Audit log | `auth_events` and money/stock tables append-only (ORM guard + PostgreSQL grants) | `test_postgres.py::test_app_role_cannot_update_or_delete_append_only_tables` | ok (tested) |
| Encryption at rest | none defined for the server DB or backups; edge SQLCipher only in the ADR | — | P1 |
| Backups / restore drill | **none** | — | **P0** before any pilot |
| Device identity | enrollment codes are single-use and expire; revoke takes effect at once | `test_auth.py` | ok (tested) |
| Threat model | **none** (SEC-0) | — | P1 |
| CI agent workflows | PR #9 adds a scheduled Claude action with write permissions | §3 | P1: require branch protection + owner review first |

---

## 7. Golden path (office → chef → stock → menu → POS → payment → KDS → ledger → reporting)

| Handoff | Status | Evidence |
| --- | --- | --- |
| Office creates item | **VERIFIED** (stock item via purchase invoice form) | `tests_e2e/test_inventory_ui.py::test_invoice_to_stock_to_recipe_to_sale` |
| Chef recipe (versioned, live cost) | **VERIFIED** | same test; `tests/test_inventory.py` |
| Inventory stock (invoice post, moving average) | **VERIFIED** | same; `test_inventory.py` owner acceptance (10 kg → 6 kg, €30, COGS €20) |
| **Menu publish** (create a sellable product/button) | **MISSING** | there is no product/menu create endpoint; offers come from seed data only |
| POS button | **VERIFIED** for catalog products | `tests_e2e/test_cashier_ui.py` |
| TV menu board | **MISSING** | — |
| QR / guest ordering | **MISSING** | — |
| Sale | **VERIFIED** | E2–E5 |
| Payment: cash | **VERIFIED (simulated)** | `test_sales.py`, e2e |
| Payment: card terminal | **MOCK** (PR #26, not in `main`) | `test_payments.py` (PR) |
| Customer receipt | text **VERIFIED**; printed **MISSING** | `receipt.txt` endpoint tests |
| KDS ticket | **VERIFIED** (table orders) | `test_orders.py`, `test_cross_device_orders.py` |
| Inventory consumption on sale | **VERIFIED** (exactly once, concurrent workers) | `test_inventory.py::test_concurrent_sales_and_workers_consume_exactly` |
| Ledger / accounting | `books.export` queue **VERIFIED** in `main`; journal preview **VERIFIED only in a cross-module harness** (CONNECT not in `main`); real posting **MISSING** | `test_events.py`; E16 |
| Manager reporting | daily summary + margin **VERIFIED**; dashboard **MISSING** | `test_drawer_and_reports.py`; inventory margin tests |

**Verdict:** the golden path is **broken at "menu publish"**, and again at TV/QR, card terminal, printing and real accounting.

---

## 8. UX — measured (E19, E20)

Run setup: local loopback, Playwright Chromium, demo data. The wall time includes automation and server work, **not human think time**. There was no user research and these are not field benchmarks.

| Metric | Phone 390×844 | Tablet 1024×768 |
| --- | --- | --- |
| First load to catalog visible | 228 ms | 161 ms |
| Open drawer (once per shift) | 2 taps + float entry | 2 taps + float entry |
| 2-item cash sale, quick tender, to next customer | **6 taps** | **6 taps** |
| Same, automated wall time (median / max of 5) | 384 / 440 ms | 420 / 448 ms |
| API `POST /sales/cash` p50 / p95 (n=50) | 14.4 / 17.9 ms | 13.2 / 18.0 ms |
| WCAG 2 A/AA violations (axe) | color-contrast (serious): express 27, tables 35, kitchen 3 nodes | — |

**Setup complexity (observed):** a fresh install needs, all of them by hand:
- PostgreSQL and Alembic;
- a secret key;
- device enrollment codes;
- staff creation through the admin API.

The office has **no setup UI** for products or the organization.

---

## 9. Failure scenarios

| Scenario | Status | Evidence |
| --- | --- | --- |
| Offline sale and reconnect | **MISSING** (the till correctly refuses and shows offline; it cannot sell) | `test_offline_is_visible_and_nothing_is_faked` |
| Duplicate / out-of-order events | **VERIFIED** (inbox dedup, per-aggregate order, block policy, DLQ) | `test_events.py` |
| Lost response, double tap, retry | **VERIFIED** | `test_lost_response_after_commit_retry_returns_same_sale`, `test_same_idempotency_key_in_parallel_creates_one_sale` |
| Power loss mid-sale | browser crash: **VERIFIED**; server process kill: **VERIFIED** (E18); OS power cut / disk: **NOT TESTED** | `test_crash_recovery*.py`; E18 |
| Terminal timeout / unknown | **MOCK** (PR #26: unknown → reconcile) | `test_card_unknown_blocks_settlement_until_reconciled` (PR) |
| Printer offline | **MISSING** | — |
| TV disconnected | **MISSING** | — |
| Two registers concurrent | **VERIFIED** | `test_receipt_numbers_unique_and_gap_free_under_parallel_load`, `test_two_registers_are_isolated` |
| Two outlets concurrent | **NOT TESTABLE YET** (no outlet entity) | ADR-0013 |
| Expired ingredient | **MISSING** (no lot/expiry) | — |
| Stock conflict | **VERIFIED** (no lost update; negative stock flagged) | `test_parallel_receipts_and_production_lose_no_update`, `test_negative_stock_is_allowed_but_flagged` |
| Device / staff revoke | **VERIFIED** | `test_logout_and_deactivation_and_device_revoke_take_effect_at_once` |
| Backup restore | **MISSING** | — |

---

## 10. 2027 Tallinn Zoo — one-site pilot readiness

### Blocking items

| ID | Item | Owner | Evidence needed to close |
| --- | --- | --- | --- |
| B1 | Offline edge O1–O6 (#29) | Claude | the #29 acceptance demo with exact counts |
| B2 | Central server hosting: production environment, TLS, monitoring, on-call (none exists) | owner + Claude | runbook + uptime/alerting evidence |
| B3 | Backup + restore drill, RPO/RTO | Claude | automated restore test + a manual drill report |
| B4 | Card terminal: provider chosen, sandbox adapter, certified device | owner (contract) + Claude | sandbox run + hardware in the loop |
| B5 | Receipt + kitchen printing (incl. a shared printer) | Claude | hardware-in-the-loop test |
| B6 | Menu / product management (publish to POS) | Claude | e2e test: create → sell |
| B7 | Organization hierarchy (entity / site / outlet) | Claude + owner data | migration + RLS tests; real zoo outlet list |
| B8 | Legal / accounting: receipt fields, VAT, numbering, CONNECT export | owner's accountant + AION | signed acceptance |
| B9 | Security gate: branch protection, private repo decision, threat model, website `next` critical | owner + Claude | settings evidence, a SEC-0 document, `npm audit` clean |

### Hardware list (to confirm; nothing purchased or verified)

| Item | Quantity | Note |
| --- | --- | --- |
| Till: Windows touch terminal or mini-PC + touch screen | 1 per register | Windows was the owner's choice; the Android spike is pending |
| 80 mm ESC/POS receipt printer, LAN | 1 per outlet; may be shared for kitchen tickets (#25) | — |
| Cash drawer (printer-kick) | 1 per register | — |
| Card terminal | 1 per register | provider TBD: Revolut / Nexi / Worldline; LHV is not verified as an ECR API (#25 research) |
| KDS tablet or kitchen printer | per kitchen station | — |
| Router with LTE failover + UPS | per outlet | the edge node still needs local power |

### Work packages

These are estimates. Assumptions: Claude implements with owner reviews, one pilot outlet, the provider sandbox is available, and hardware is on site. Confidence: L = low, M = medium.

| WP | Content | Estimate | Confidence |
| --- | --- | --- | --- |
| WP1 | O1 device journal + per-register numbering + sync push/ingest | 2–3 weeks | M |
| WP2 | O2/O3 snapshot, offline grant, leases, print queue | 3–4 weeks | L–M |
| WP3 | O5 Windows packaging (Tauri + sidecar, SQLCipher) | 2–4 weeks | L |
| WP4 | Menu / product management + publish | 1–2 weeks | M |
| WP5 | Hierarchy migration 0006/0007 | 1–2 weeks | M |
| WP6 | Backup / restore + hosting runbook | 1–2 weeks | M |
| WP7 | Terminal adapter (sandbox) | 2–3 weeks after provider access | L |
| WP8 | Printing bridge + templates | 2–3 weeks | L |
| WP9 | Security gate items | 1 week, plus owner settings | M |
| WP10 | Pilot hardening + shadow operation | 2–4 weeks | L |

### Acceptance gates

1. **Gate 1:** all P0 rows in §6 and §11 closed.
2. **Gate 2:** the #29 offline demo passes.
3. **Gate 3:** hardware in the loop (printer + terminal).
4. **Gate 4:** accountant sign-off.
5. **Gate 5:** a 2-week shadow pilot at one outlet with daily reconciliation.
6. **Then:** expansion to more outlets.

---

## 11. Risk register

| ID | Risk | Likelihood | Impact | Mitigation | Owner |
| --- | --- | --- | --- | --- | --- |
| R-01 | Pilot without offline loses sales during a network outage | high | high | WP1–WP3 before the pilot | Claude |
| R-02 | Unprotected `main` on a public repo: an unreviewed change auto-deploys the website | medium | high | branch protection (required checks + review); private repo decision | owner |
| R-03 | Critical `next` vulnerability on the live website | known | high | website dependency PR, tested on a Vercel preview, owner-approved deploy | owner / Claude |
| R-04 | Data loss without backups | medium | high | WP6 | Claude |
| R-05 | Terminal provider onboarding delays the pilot | high | high | choose a provider now; mock-first adapter | owner |
| R-06 | No production hosting for the central server | certain | high | hosting decision + runbook (WP6) | owner |
| R-07 | CONNECT v2 gap blocks merging C3 payments | high | medium | AION v2 adapter | AION |
| R-08 | Scope expansion (#31–#42) ahead of pilot essentials | high | medium | hold P2 modules until Gate 5 | owner |
| R-09 | Accessibility: contrast failures | known | medium | CSS token fix + axe in CI | Claude |
| R-10 | Agent workflows with write tokens (PR #9) | medium | medium | merge only after branch protection | owner / AION |

---

## 12. Two-week action plan (ordered)

1. **Owner (day 1):**
   - enable branch protection on `main` (require `axar-pos` checks + one review);
   - decide on repository visibility (#1);
   - approve a website dependency PR for `next`;
   - choose a card terminal provider to request sandbox access from.
2. **Claude (days 1–3):** the 3 PRs below.
3. **Claude (days 3–10):** WP1 (O1) as small draft PRs: journal → numbering → push/ingest → 2-node acceptance test.
4. **Claude (days 5–10):** WP5 hierarchy migration 0006 (additive + backfill + verification).
5. **AION:** CONNECT v2 adapter, then #8 retargeted to `main`.
6. **Claude (days 10–14):**
   - accessibility contrast fix;
   - menu/product create API + UI (WP4 start);
   - #26 merge once the v2 adapter lands.

## 13. Next 3 smallest draft PRs

1. **SEC gate in CI:**
   - secret scanning (gitleaks, pinned) + bandit job;
   - `SEC-0` threat model and risk register document;
   - committed versions of the E18 kill-9 durability check and the E20 axe check as tests.
2. **Backup / restore drill:**
   - `pg_dump` / `pg_restore` scripts;
   - a PostgreSQL test that restores into a fresh database and proves counts, sums and receipt sequences match;
   - an RPO/RTO statement.
3. **O1a device journal:**
   - device journal table (ULID, `device_seq`, hash chain) written in the cash-sale transaction;
   - per-register receipt series behind a setting;
   - tests for gap-free sequences and tamper detection.

   No sync yet.
