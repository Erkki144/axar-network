# 10 — SEC-0 threat model and security risk register (#34)

Status: **v0.1, 2026-11-25.** It covers the code in `main` @ `642e83e`, plus the planned edge node (ADR-0012) where noted. It does not claim the system is secure.

- Controls marked **tested** name the test that proves them.
- Everything else is a gap, with an owner and a work package.
- Evidence IDs (E…) refer to the [readiness audit](AXAR-ONE-READINESS-AUDIT-2026-11-25.md).

## 1. Assets

| Asset | Why it matters | Where |
| --- | --- | --- |
| A1 Money records (sales, refunds, drawer totals, receipt numbers) | revenue, tax, fraud | `sales`, `refunds`, `drawer_sessions`, `tenant_counters` |
| A2 Stock ledger + costing | COGS, theft detection | `stock_movements`, `stock_balances` |
| A3 Credentials | takeover of tills / back office | `staff` (argon2id PIN and password hashes, TOTP secret encrypted with `AXAR_POS_SECRET_KEY`), `devices`, `auth_sessions` (sha256 token hashes) |
| A4 Tenant isolation | one customer reading or changing another's data | PostgreSQL RLS + `tenant_id` |
| A5 Event stream / export queue | what accounting receives | `outbox_events`, `export_queue` |
| A6 Personal data (staff names; future PEOPLE/HR; guest data) | GDPR | `staff`; planned #33/#18 |
| A7 Source code, CI and the deploy pipeline | supply chain; the website auto-deploys from `main` | GitHub, Vercel |
| A8 Future: edge device database and signing keys | offline sales, device impersonation | ADR-0012 |

## 2. Actors

- **External attacker** (internet).
- **Malicious or curious tenant.**
- **Dishonest cashier or waiter.**
- **Dishonest manager.**
- **Stolen or lost till.**
- **Compromised CI / dependency.**
- **AI agents with repository access:** Claude, AION, and the workflows in PR #9.
- **Accidental operator error.**

## 3. Trust boundaries

```
[browser UI] --HTTPS--> [POS API (FastAPI)] --SQL as axar_app (RLS)--> [PostgreSQL]
                              |  owner/admin connection only for migrations & seeding
[till device token + staff session]        [worker: consumers -> export_queue] --> (CONNECT, planned)
[GitHub repo (public)] --CI--> [Actions]   [main] --auto--> [Vercel production: website]
[edge node + local SQLite] <--sync--> [central]   (planned, ADR-0012)
```

## 4. STRIDE by boundary

| # | Threat | Boundary | Control today | Evidence | Gap / action |
| --- | --- | --- | --- | --- | --- |
| T1 | **S**poof a cashier by guessing a PIN | UI → API | argon2id PINs, lockout per staff and per device with row locks, backoff | `test_pin_brute_force_locks_staff_and_device`, `test_concurrent_wrong_pins_are_all_counted` | — |
| T2 | Spoof a manager | UI → API | password + TOTP (replay rejected); a PIN is not enough for admin | `test_password_login_needs_correct_totp_and_rejects_replay` | passkeys/WebAuthn later (#34) |
| T3 | Spoof a device / use a stolen till | device → API | enrollment codes are single-use and expire; device token stored hashed; revoke takes effect at once | `test_enrollment_code_is_single_use_and_expires`, `test_logout_and_deactivation_and_device_revoke_take_effect_at_once` | offline-grant expiry for edge (ADR-0012) |
| T4 | Spoof the tenant through request headers | API | the tenant comes from the session; demo headers are off by default and refused by the pilot profile | `test_tenant_comes_from_the_session_never_the_request`; pilot-guard test in `test_auth.py` | — |
| T5 | **T**amper with sales or the stock history | API → DB | append-only through the ORM guard **and** PostgreSQL grants (no UPDATE/DELETE for `axar_app`) | `test_postgres.py::test_app_role_cannot_update_or_delete_append_only_tables` | a hash chain for edge journals (ADR-0012) |
| T6 | Tamper with prices or VAT in the client | UI → API | the server prices everything; quote and sale share one pricing function; no money logic in the UI | `test_quote_and_ui_assets.py::test_ui_has_no_client_side_money_authority` | — |
| T7 | Tamper with the code in `main` | GitHub | CI runs on PRs, but **`main` is unprotected** | E12 | **owner: enable branch protection (required checks + review)** |
| T8 | Tamper through a dependency (supply chain) | CI | pinned Python dependencies; `pip-audit --strict` in CI; checksum-verified gitleaks binary | E7; this PR | SBOM, signed releases (SEC-3 later) |
| T9 | **R**epudiate a refund or a variance | API | step-up approval bound to the exact request, by a different manager; `auth_events` append-only | `test_approval_is_bound_to_the_exact_request_and_expires` | an audit-log export for the accountant (#32) |
| T10 | **I**nformation disclosure across tenants | API → DB | RLS with a non-owner role; fail-closed without a tenant; negative tests on every tenant table | `test_rls_hides_other_tenants_rows_even_without_where`, `test_tenancy_and_identity.py` | ABAC per legal entity and outlet (ADR-0013) |
| T11 | Disclosure through secrets in the repository | GitHub (public) | no secrets in tree or history (E9 + gitleaks over the full history); **gitleaks in CI (this PR)** | `security.yml` `secret-scan` | **owner: repository visibility decision (#1)** |
| T12 | Disclosure through logs or event errors | worker | errors stored as type + fingerprint, not raw messages | `test_events.py` redaction test | central log access controls (hosting) |
| T13 | Disclosure through the data at rest | DB / backups | **none defined** | — | encryption at rest + encrypted backups (SEC-2); SQLCipher for edge (O5) |
| T14 | **D**enial of service: login flood | API | rate limit on enrolment and password login (20 per 5 min, per process); durable lockouts in the database | `auth.RateLimiter` | per-process only: needs an edge/WAF limit at hosting |
| T15 | DoS: lost network stops sales | UI ↔ API | the UI refuses and shows offline (safe, but no sales) | `test_offline_is_visible_and_nothing_is_faked` | **offline-first (#29)** |
| T16 | DoS: server crash mid-sale | API → DB | committed sales survive a process kill; retries are idempotent | **`tests_e2e/test_durability.py` (this PR)** | OS power-cut / fsync fault tests on the edge (O1) |
| T17 | DoS: data loss | DB | **no backups** | — | **SEC-2 backup + restore drill (next PR)** |
| T18 | **E**levation: a cashier performs manager actions | API | per-permission role checks; refunds need the manager role and step-up | `test_role_matrix`, `test_refunds.py` | — |
| T19 | Elevation: the app role bypasses RLS | DB | `axar_app` has neither superuser nor BYPASSRLS; ownership sits with a separate role | `test_app_role_is_not_owner_and_cannot_bypass_rls` | — |
| T20 | Elevation: an AI agent workflow with write tokens | CI | none in `main`; PR #9 adds a scheduled Claude action with `contents: write` | audit §3 | merge #9 only after branch protection + owner approval |
| T21 | Website (`next` critical CVE) used as a foothold into a shared Vercel account | Vercel | none | E11 | **owner-approved website dependency update** |

## 5. Risk register (security)

The risk IDs continue the audit's numbering.

| ID | Risk | L | I | Status | Owner | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| S-01 | Unprotected `main` | M | H | open | owner | branch protection settings |
| S-02 | Public repository | — | M–H | decision pending (#1) | owner | decide |
| S-03 | Website critical CVE | known | H | open | owner + Claude | website dependency PR, preview-tested |
| S-04 | No backups / restore drill | M | H | open | Claude | SEC-2 PR (next) |
| S-05 | No encryption-at-rest definition | M | H | open | owner (hosting) + Claude | hosting decision |
| S-06 | Offline edge security (keys, SQLCipher, grants) | — | H | design only (ADR-0012) | Claude | O1–O5 |
| S-07 | Agent workflows with write access (PR #9) | M | M | open | owner / AION | gate behind S-01 |
| S-08 | No independent penetration test | — | H | planned (SEC-5) | owner | before broad rollout |
| S-09 | Secret leak through future commits | L | H | **mitigated** by the CI secret scan (this PR) | — | — |
| S-10 | Python SAST regressions | L | M | **mitigated** by bandit in CI (this PR) | — | — |

## 6. Owner decisions needed

1. **Branch protection on `main`:** require the `axar-pos`, `security` and Vercel checks plus one review. This is a settings change only the owner can make.
2. **Repository visibility (#1).**
3. **Hosting for the central server:** provider, region (EU), encryption at rest, backup storage.
4. **Website dependency update** for the `next` critical vulnerability. It goes through a preview, then a production deploy approved by the owner.
