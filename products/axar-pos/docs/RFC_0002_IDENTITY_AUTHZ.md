# RFC-0002 — Identity, authorization and PostgreSQL migration (issue #2)

Status: **Option A + strong back-office login implemented in slice 6** (see
[SLICE_6_AUTH.md](SLICE_6_AUTH.md)); PostgreSQL part in slice 5. Original
draft text below. No vendor is selected, no credentials are
configured.

## 1. Why

Today every tenant-scoped endpoint trusts `X-Tenant-ID`, `X-Cashier-ID` and
`X-Role` when `AXAR_POS_DEMO_IDENTITY=1`, and refuses (503) otherwise. That is
safe only because the service is local and off by default. Before any shared
or external use we need verified identity, server-side roles, scoped rights,
PostgreSQL with migrations, and concurrency tests that run on PostgreSQL.

## 2. Options

### A. POS-native: enrolled device + staff PIN + server sessions

- A register's device is **enrolled** once by a manager/owner and receives a
  device credential (rotating device token; mTLS later). The device is bound
  to exactly one tenant, location and register.
- Staff unlock the till with a **PIN that is valid only on enrolled devices of
  their tenant**. PINs are hashed with argon2id, never logged.
- The server issues a **short-lived session** (opaque token, stored server-side,
  revocable) bound to `(staff, device, register)`.
- Manager approval (refunds, drawer variance) is a **step-up**: the manager
  enters their own PIN on the same device; the server verifies it and issues a
  single-use approval valid for ~60 s and for one specific action.

Pros: fast shift switching at a shared till, no external dependency, possible
future offline mode. Cons: low PIN entropy has to be compensated by device
binding and rate limiting; we own credential storage.

### B. OIDC provider + staff mapping

- Staff log in through an OIDC provider; the verified `sub` claim maps to a
  staff record that carries tenant and roles. Roles always come from our
  database, never from token claims we do not control.

Pros: proven login flows, MFA, password reset out of the box. Cons: awkward on
a shared till (full login per shift change), no offline story, vendor coupling,
customer data leaves our system.

### Recommendation (vendor-neutral)

**Hybrid.** Use OIDC (provider chosen later) for owner/manager back-office
access and for **device enrollment**. Use device-bound PIN sessions (option A)
at the till for cashiers and for manager step-up approval. Owner decides the
OIDC provider separately; the till flow does not depend on that choice.

## 3. Threat model

| Threat | Mitigation | Negative test |
| --- | --- | --- |
| Role spoofing via headers | Roles only from server-side staff record; `X-Role`/`X-Tenant-ID`/`X-Cashier-ID` ignored when demo mode is off | prod mode + forged `X-Role: owner` → refund 401/403, nothing written |
| Tenant switching | Tenant derived from the session/device, never from request input; every query tenant-filtered (exists) plus PostgreSQL RLS as a second wall | session of tenant A + IDs of tenant B → 404 on all read/write endpoints, incl. raw SQL path with RLS |
| Stolen PIN | PIN valid only on enrolled devices of that tenant; device credential required; audit of every unlock | correct PIN from un-enrolled device → 401; from device of other tenant → 401 |
| PIN brute force | Per-staff and per-device lockout (5 failures → exponential backoff), argon2id, global throttle, alert | 6th wrong PIN → 429 and lockout even with correct PIN until backoff ends |
| Shared terminal, walk-away | Auto-lock after inactivity; every financial record stores `staff_id`, `device_id`, `session_id` | expired session → 401; records carry the acting staff and device |
| Employee termination | Deactivation revokes all sessions immediately (server-side session lookup on every request, no long-lived bearer tokens) | deactivate staff mid-session → next request 401 |
| Refund approved by the same person | Refund requires a step-up approval from a **different** verified staff member with manager/owner role in the same tenant and location, single use, ≤ 60 s, bound to the refund request hash | cashier approving own refund → 403; approval reused or expired → 403; approval for other amount → 403 |
| Register reassignment | Device → register binding changed only by manager/owner; blocked while the register's drawer is open; existing sessions revoked | reassign with open drawer → 409; old session on reassigned device → 401 |
| Session theft / replay | Opaque tokens, rotated on privilege change, `Authorization` header (no cookies) for the till API; back-office cookies `SameSite=Strict` + CSRF token | reused rotated token → 401 |
| Offline sessions | **Out of scope for v1.** If added later: signed short-TTL offline grants, sales-only under a cap, no refunds or drawer close offline, reconciliation on reconnect | — |

## 4. Authorization matrix (proposed)

Scope column: **T** = same tenant, **L** = same location, **R** = the register
bound to the session's device.

| Endpoint | cashier | manager | owner | Scope |
| --- | --- | --- | --- | --- |
| `GET /products`, `GET /registers` | ✓ | ✓ | ✓ | T |
| `POST /drawer/open` | ✓ | ✓ | ✓ | R |
| `POST /drawer/{id}/close` | ✓ (variance 0) | ✓ | ✓ | R; non-zero variance needs manager step-up |
| `POST /sales/cash` | ✓ | ✓ | ✓ | R |
| `GET /sales/{id}`, receipts | ✓ | ✓ | ✓ | T (cashier: own location) |
| `POST /sales/{id}/refunds` | request only | ✓ | ✓ | R; needs step-up approval by a *different* manager/owner |
| `GET /reports/daily` | — | ✓ | ✓ | manager: L, owner: T |
| catalog / staff / device admin (new) | — | location staff only | ✓ | T |

## 5. Data model additions

`staff` (tenant, name, active, pin_hash, failed_attempts, locked_until),
`staff_roles` (staff, role, location scope), `devices` (tenant, location,
register, credential hash, enrolled_by, revoked_at), `sessions` (staff, device,
register, created, last_seen, expires, revoked_at), `approvals` (approver,
action, request hash, expires, used_at), `auth_events` (append-only audit).

## 6. PostgreSQL + Alembic plan

1. Add Alembic; generate a **baseline** migration from the current models.
   Check it against `create_all` with a schema diff in CI.
2. Types: money `BIGINT` cents, timestamps `timestamptz`, partial unique indexes
   (already declared with `postgresql_where`).
3. Receipt and refund numbers: per-tenant counter row updated with
   `UPDATE … RETURNING` (row lock) instead of `max()+1`.
4. RLS: `ALTER TABLE … ENABLE ROW LEVEL SECURITY` on every tenant table, policy
   `tenant_id = current_setting('app.tenant_id')`, set per transaction with
   `SET LOCAL`. The app's DB role is not the table owner and has no `BYPASSRLS`.
5. SQLite stays for local demo and fast tests; the `BEGIN IMMEDIATE` hook
   applies only to SQLite.
6. Every later schema change ships as an Alembic migration with an upgrade test
   from the previous revision. Data migrations need human review (AGENTS.md).

## 7. CI and test plan

- GitHub Actions job with a `postgres:16` service. Run the whole suite twice:
  SQLite and PostgreSQL (`AXAR_POS_DATABASE_URL`).
- **Real two-connection concurrency tests on PostgreSQL**. On SQLite these
  interleavings cannot happen, which is why some guards are covered there only
  by deterministic same-session simulations.
  - sale vs drawer close: connection 1 claims the drawer and holds the
    transaction; connection 2 closes; assert the close waits and includes the
    sale, or the sale is rejected after the close commits;
  - two concurrent partial refunds of the same line / same sale (CAS loser gets
    409, totals exact);
  - concurrent receipt numbering (no gaps from failures, no duplicates).
- RLS tests: a raw query under tenant A's `app.tenant_id` cannot see B's rows.
- Auth negative tests from section 3, plus: demo mode cannot be enabled when an
  identity provider is configured (fail closed at startup).

## 8. Decisions needed from the owner

1. Accept the hybrid model, or choose A only / B only.
2. OIDC provider for back-office (later, separately): e.g. Supabase Auth,
   Auth0, Keycloak (self-hosted).
3. Hosting for PostgreSQL (later; nothing is provisioned by this RFC).
4. Where the work lands: the private repository (issue #1) is strongly
   recommended before real identity code, since this repository is public.
