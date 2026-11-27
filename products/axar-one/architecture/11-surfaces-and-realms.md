# 11 — Surfaces, realms and who uses what

Status: **Proposed** (owner review). Builds on `01-system-context.md` (modules) and `06-trust-and-permissions.md` (boundaries).

## Kokkuvõte eesti keeles

AXAR ONE-l on **üks keskserver** (üks andmebaas, üks sündmuste ahel, üks kooskõlakontroll) ja **neli kasutajaliidest**.
Liidesed on vaated samadele andmetele. Need erinevad selle poolest, kes sisse logib ja mida ta tohib teha.

| # | Liides | Kes kasutab | Kus töötab | Olek |
|---|---|---|---|---|
| 0 | **Keskserver (CORE)** | keegi otse; kõik liidesed kutsuvad selle API-t | pilves / serveris | olemas (`products/axar-pos`) |
| 1 | **Platvormi haldus (PLATFORM)** | AXAR-i töötajad: tugi, arendus, arveldus | eraldi aadress, eraldi sisselogimine | puudub, vaja enne teist klienti |
| 2 | **Kontor (OFFICE)** | kliendi omanik, juht, raamatupidaja, laohoidja, kokk | veebibrauser, ka tahvel ja telefon | osaliselt (`static/one/office.mjs`, `inventory.mjs`) |
| 3 | **Kassa (TILL)** | kassapidaja, teenindaja, juht kinnitusteks | paigaldatav: Windows / Android, töötab kuni 72 h ilma võrguta | veebis olemas (`static/one/app.mjs`), paigaldus puudub (P2-1) |
| 4 | **Lisaekraanid (SCREENS)** | köök, klient, külaline | sama kassatarkvara teise rolliga seadmel; külaline telefonis | köök kassa vaatena olemas, ülejäänud puudub |

**Põhimõte:** äriloogika on ainult keskserveris. Ükski liides ei arvuta hindu, makse ega laoseisu ise. Erand on võrguühenduseta kassa,
mis kasutab keskserveri viimast versiooni ja saadab kõik hiljem kontrolli (ADR-0010).

---

## 1. Surfaces

### 0. CORE — central server
- Modular monolith + PostgreSQL (ADR-0002), outbox/inbox (ADR-0004), reconciliation (ADR-0005).
- Owns **every** write. Surfaces send commands and read projections; they never write tables.
- Exposes three API families, one per realm (§2): `/api/platform/*`, `/api/v1/*` (office + till), `/api/guest/*` (planned #18).

### 1. PLATFORM — operator console (AXAR staff only)
| Area | Content | Why it is not in the office |
|---|---|---|
| Tenants | create tenant, legal entities, locations; suspend / reactivate | a customer must not create or see other customers |
| Licences & modules | package, enabled modules, register count, billing | commercial, AXAR-owned |
| Integration credentials | card-terminal provider keys, accounting API keys, courier keys; **write-only**, encrypted, rotated | secrets must never be readable by anyone after entry |
| Health (NERVE) | event lag, dead letters, reconciliation failures per tenant, backups, versions | cross-tenant operations view |
| Releases | which till version each tenant runs, staged rollout | fleet management |
| Support access | time-limited, **customer-approved**, read-only by default, fully audited | privacy and trust |

### 2. OFFICE — customer back office
Master data, documents, reports, settings, staff and permissions, module activation (the customer switches on a module the platform has already
connected). Full requirements: `docs/BACKOFFICE_REQUIREMENTS_ET.md`.

### 3. TILL — installed point-of-sale client
Selling, tables, payments, drawer and shift, manager approvals with the manager's own PIN **on the same till**, kitchen send, receipt.
Offline up to 72 h (ADR-0010), hash-chained device journal, sync exactly once. Talks to the card terminal and printers locally.

### 4. SCREENS — role-bound devices
Kitchen display (KDS), customer-facing display, menu TV, order-ready board, guest QR web ordering (#18).
One client build; the **device role** chosen at enrolment decides the view. No separate apps to maintain.

## 2. Identity realms (three separate worlds)

| Realm | Who | How they sign in | What they can reach | Never |
|---|---|---|---|---|
| **platform** | AXAR staff | email + password + hardware key / TOTP, IP allow-list | PLATFORM only | appear in a customer's staff list; read customer data without an approved support session |
| **tenant** | customer's office users | email + password + TOTP (managers) | OFFICE of their own tenant, scoped by permission group and location | reach PLATFORM or another tenant |
| **device** | cashier, waiter, kitchen | PIN or staff card **on an enrolled device** | TILL / SCREENS of the device's location | sign in on an unenrolled device |

Built today: tenant + device realms (ADR-0009, `auth.py`, C10). Missing: platform realm, permission groups as data (roadmap P1-8).

## 3. Rules

1. **One source of truth.** Every surface reads the same registers (`12-register-map-and-journeys.md`).
2. **Server decides.** Prices, discounts, VAT, totals and permissions are computed in CORE; surfaces display them.
3. **Same code for office and till** while they share the web client; the installed till wraps the same UI (ADR-0012, PR #30).
4. **Platform is built when needed:** before the second customer. Until then tenants are created with the admin CLI.
5. **Every surface action is attributable:** realm, user, device, location and session are on every event (envelope v2).

## 4. Implemented (#76, 27.11.2026)

- **Two applications:** the till at `/one/` (alias `/pos/`) and the office at `/office/`. The till bundle has no office or stock code and no office navigation; the office bundle has no till money operations. Shared: the design system and display helpers (`/one/format.mjs`, styles).
- **Two logins:** till = staff PIN on an enrolled device (`kind="till"`); office = company, user name, password and TOTP (`kind="admin"`). The office token lives in memory only; logging out of one never logs out the other.
- **Server enforcement:** `axar_pos/realms.py` gives every API route one realm (`pos`, `office`, `read`, `auth`); `identity()` refuses a till token in the office and an office token at the till with `403 wrong_realm`, before any handler. A route without a realm is unreachable (fail closed).
- **Guards:** `tests/test_realms.py` (every route has a realm; negative tests with real sessions; no DB change on refusal; one person, two logins), `tests/test_quote_and_ui_assets.py` (bundles stay apart).
- **Screens:** `docs/reviews/76-screens/`.
- **Still open:** office approvals of till actions stay a one-time step-up proof on the till (`/auth/approvals`); Menu Studio publishing with per-till ACK is #40.

## 5. PIN is the identity, the device is the register (#81, server part)

- `POST /api/v1/auth/pin-login` with **only** `{"pin"}` and the device token. The server finds the one staff member through a tenant-scoped blind index (HMAC with a server pepper, `UNIQUE(tenant_id, pin_index)`), verifies Argon2 once, and opens the **device's** register. The client can name neither the person nor the register (extra fields → 422).
- **Register grants** (`staff_register_grants`, set in OFFICE: `PUT /api/v1/admin/staff/{id}/registers`) are checked at login **and on every request**. A grant or role removed, or a person deactivated, ends the open till session at its next request.
- Wrong, unknown and not-granted PINs give one generic answer and count against the device (durable lockout). The PIN is never stored in clear or logged.
- Migration and key rotation: `products/axar-pos/docs/runbooks/PIN_INDEX_KEY_ROTATION.md`. Still to do: the PIN-only till screen (the name grid goes) and tap measurements; then wire the action contract (#83).
