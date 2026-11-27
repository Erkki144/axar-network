# Runbook — PIN identity index (#81): migration and key rotation

## What is stored

| Column | Content | Purpose |
|---|---|---|
| `staff.pin_hash` | Argon2id of the PIN | proves the PIN (slow, salted) |
| `staff.pin_index` | `v1:` + HMAC-SHA256(pepper, tenant_id ‖ 0x00 ‖ PIN) | finds the ONE staff member a PIN belongs to (fast, exact) |

- The pepper is derived from `AXAR_POS_SECRET_KEY` (environment, never in the database).
  A database dump alone does not map an index back to a PIN.
- `UNIQUE (tenant_id, pin_index)` is the only uniqueness rule. Two parallel assignments of one PIN:
  exactly one commits, the other gets `409 pin_unavailable`. The answer never says whose PIN it is.
- Deactivating a staff member clears `pin_index`; the PIN can be given to someone else.
- The PIN is never logged, never in a URL, never in `auth_events` (tests in `tests/test_pin_identity.py`).

## Upgrading an existing installation (migration 0011)

Existing PINs have only an Argon2 hash. A hash cannot be turned back into the PIN, so the index cannot be computed offline.

1. Deploy 0011. Every existing staff member has `pin_index = NULL`; the office staff list shows `pin_indexed: false`.
2. The pre-#81 name-grid login (`staff_id` + PIN) still works. Its first success computes the index (`auth_events: pin_index.backfill ok`).
3. If two people already share a PIN, the second backfill is refused (`pin_index.backfill conflict`). That person keeps the name-grid login until the office **generates** a new PIN (`POST /api/v1/admin/staff/{id}/pin/generate`, shown once).
4. When every active till user shows `pin_indexed: true`, switch the till UI to PIN-only (next #81 PR) and retire the name grid.

## Rotating the server key

The index key is versioned (`PIN_INDEX_KID`, today `v1`). Rotation re-keys `AXAR_POS_SECRET_KEY`, so the TOTP seeds sealed by `SecretBox` are affected as well. Plan it as one event:

1. Announce: all till users will receive a new PIN, or log in once with the name grid during the window.
2. Deploy with the new key and the next `PIN_INDEX_KID`. Rows that keep the old prefix are not found by PIN-only login.
3. For each staff member, either generate a new PIN, or let the name-grid login backfill under the new key.
4. Verify that no active staff member has an index with the old prefix, then remove the old key.

A dual-key lookup window (old and new prefix both accepted) is possible later. It is deliberately not built yet: it would make uniqueness span two keys.

## Throttling

- Each wrong PIN-only attempt counts against the **device** row (`devices.failed_pin_attempts`, `pin_locked_until`). The lock is durable across restarts and workers; it starts after 5 failures and the backoff doubles, up to 1 h.
- A wrong PIN, an unknown PIN, a PIN of someone not granted this register, and a PIN of a locked person all give the same `401`. Audit records the reason (`no_match`, `not_granted`, `staff_locked`) for the office; the till sees nothing more.
- Guessing in parallel from many tills needs many devices, and each device was enrolled by an office user.
