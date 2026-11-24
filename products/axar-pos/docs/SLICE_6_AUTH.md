# Slice 6 — server-side identity, roles and step-up (issue #2, part 2)

Status: **local / CI only.** Branch `claude/axar-pos-s8-auth-0r9ip8`, stacked
on PR #17. This implements RFC-0002 option A (enrolled device + staff PIN +
server sessions) plus a strong back-office login (password + TOTP). No external
identity provider is used. OIDC can be added later as another way to create
the same back-office session.

## Flows

| Who | How | Result |
| --- | --- | --- |
| First owner | `python -m axar_pos.cli bootstrap` on the server (owner DB connection) | owner with password (argon2id) + TOTP seed printed **once**; refuses if the tenant already has an owner |
| Owner / manager | `POST /api/v1/auth/login` `{tenant_id, login, password, totp}` | `admin` session, `mfa=true`, no device |
| Owner / manager (MFA session) | `POST /api/v1/admin/enrollment-codes` | single-use code, 80 bits, 15 min, bound to location (+ register) |
| New till | `POST /api/v1/auth/devices/enroll {code}` | device token (256 bits). Only its SHA-256 is stored. Kept in the till's `localStorage` |
| Staff at a till | `GET /api/v1/auth/device` → names; `POST /api/v1/auth/pin-login {staff_id, pin}` + `X-Device-Token` | `till` session bound to the device. Kept in `sessionStorage` |
| Every request | `Authorization: Bearer <session>` (+ `X-Device-Token` for till sessions) | server looks up the session each time: expiry, idle, revocation, staff active, device match, device not revoked |
| Refund / drawer variance | a **different** manager enters their PIN: `POST /api/v1/auth/approvals` | single-use approval, 60 s, bound to the exact request hash, this session and this device; sent as `X-Approval-Id` |

`X-Tenant-ID`, `X-Cashier-ID` and `X-Role` are ignored unless
`AXAR_POS_DEMO_IDENTITY=1`. Demo mode refuses to start if
`AXAR_POS_SECRET_KEY` is set.

## Roles and permissions

| Permission | cashier | waiter | kitchen | manager | owner |
| --- | --- | --- | --- | --- | --- |
| catalog, quote, tables list | ✓ | ✓ | ✓ | ✓ | ✓ |
| sell, pay a bill | ✓ | ✓ | | ✓ | ✓ |
| open/close drawer | ✓ (variance needs approval) | | | ✓ | ✓ |
| edit bills, send to kitchen | ✓ | ✓ | | ✓ | ✓ |
| kitchen tickets read / mark ready | ✓ / | ✓ / | ✓ / ✓ | ✓ / ✓ | ✓ / ✓ |
| request a refund | ✓ (with approval) | | | ✓ (with another manager's approval) | ✓ (same) |
| daily report (tenant-wide) | | | | tenant-wide managers | ✓ |
| back office (staff, devices, audit) | | | | MFA session, own locations | MFA session |

Scope:
- A role is tenant-wide or bound to one location. A till session gets only the roles valid at the device's location.
- Money and drawer actions are allowed only on the register bound to the device.
- A till sees only its own location's tables, bills, tickets and sales.
- A back-office session has no device, so it cannot sell (`device_required`).

## Threat model (RFC-0002 §3) → tests (`tests/test_auth.py`)

| Threat | Test |
| --- | --- |
| role spoofing via headers | `test_forged_identity_headers_grant_nothing`, `test_no_implicit_demo_identity_by_default` |
| tenant switching | `test_tenant_comes_from_the_session_never_the_request`, RLS `test_rls_hides_other_tenants_staff_on_postgres` |
| stolen PIN | `test_correct_pin_from_unenrolled_or_foreign_device_is_rejected`, `test_pin_works_only_at_staff_location` |
| PIN brute force | `test_pin_brute_force_locks_staff_and_device` (locks staff **and** device; correct PIN refused while locked), `test_weak_pins_are_refused` |
| password / TOTP guessing, replay | `test_password_login_needs_correct_totp_and_rejects_replay`, `test_password_login_lockout_even_with_correct_credentials`, `test_unknown_login_and_other_tenant_owner_get_same_answer` |
| walk-away / stale session | `test_idle_and_absolute_expiry`, `test_till_session_is_bound_to_its_device` |
| termination / lost device | `test_logout_and_deactivation_and_device_revoke_take_effect_at_once`, `test_inactive_staff_is_refused_even_if_a_session_was_not_revoked` |
| self-approved refund | `test_manager_cannot_approve_own_refund`, `test_refund_needs_a_different_managers_approval` |
| approval reuse / tampering | `test_approval_is_bound_to_the_exact_request_and_expires`, `test_approval_cannot_be_used_from_another_session` |
| cash variance without oversight | `test_drawer_variance_needs_approval_for_cashier` |
| privilege escalation in back office | `test_manager_cannot_grant_owner_or_manage_other_location`, `test_role_matrix`, `test_enrollment_needs_mfa_back_office_session` |
| secrets at rest | `test_totp_secret_is_encrypted_at_rest`, `test_device_token_is_stored_hashed`, `test_session_tokens_are_stored_hashed`, `test_pin_is_never_returned_or_logged` |
| audit tampering | `test_auth_events_are_recorded_and_append_only` + PostgreSQL grants (no UPDATE/DELETE on `auth_events`) |

**Mutation checks** (each mutant made at least one test fail):
- device binding removed;
- self-approval allowed;
- approval not bound to the request;
- PIN lockout removed;
- idle timeout removed;
- staff `active` check removed.

## Storage of secrets

- PINs and passwords: argon2id (`time_cost=2`, 19 MiB, OWASP). Verification does the same work when the account does not exist.
- Device tokens, session tokens and enrolment codes: SHA-256 of a high-entropy random value.
- TOTP seeds: AES-256-GCM with `AXAR_POS_SECRET_KEY`; the staff id is the associated data.
- The key is never committed. Generate it with `python -m axar_pos.cli gen-secret-key` and keep it in the secret store.

## PostgreSQL (migration `0002`)

- RLS on `staff`, `staff_roles`, `approvals` and `auth_events`. A failed attempt for an unknown tenant is audited with `tenant_id NULL`.
- `devices`, `enrollment_codes` and `auth_sessions` are looked up by token hash *before* the tenant is known, so RLS cannot filter them. Every query on them is by hash or carries an explicit tenant filter. The app role has no DELETE on them.
- `auth_events` is append-only for the app role.
- `sales` and `refunds` record `device_id` and `session_id` next to `cashier_id`.

## UI (AXAR ONE)

- In server mode, the screens are: enrol device → choose name + PIN → work.
- The "Lukusta" button ends the session.
- Any 401 locks the till. A pending money operation stays in the journal and is resumed with the same key after the next login.
- In the refund dialog, the requester chooses another manager, who enters their PIN.
- When closing the drawer with a variance, the server answers `approval_required` and the UI asks for a manager's PIN.
- `approval_invalid` counts as a definitive answer: the server checks the key before the approval. A plain 403 does not, because the role is checked before the key.

## Known limits

- The rate limit on enrolment and password login is per process (in memory). The durable per-account lockouts are in the database.
- No back-office web UI yet. The admin API is complete and covered by tests; owners use the API or CLI.
- No WebAuthn/passkeys yet (proposed follow-up). TOTP is the second factor.
- Location-scoped managers cannot see the tenant-wide daily report. A per-location report is a follow-up.
- Offline sessions are out of scope (RFC-0002).
