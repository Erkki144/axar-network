# Database migrations — numbering rules

Alembic migrations live in `axar_pos/migrations/versions/`. Each file is named
`NNNN_<topic>.py`, with `revision = "NNNN"` and `down_revision` = the previous
number, so the history is one straight line.

## Rules
1. **A migration that is on `main` is never changed, renumbered or removed.**
   Databases may already have it applied. Fixes go into a new migration.
2. **Exactly one head.** Two open PRs that each add the next number create two
   heads. The PR that merges second renumbers *its own* migration to the next
   free number and points `down_revision` at the newly merged one.
3. Both rules are enforced by `tests/test_migration_history.py`
   (`test_single_head_and_linear_chain`,
   `test_revisions_already_released_in_main_are_unchanged`), which runs in CI.
4. Every migration supports `upgrade` and `downgrade`; CI runs
   up → down → up plus `alembic check` on an empty PostgreSQL database.

## Register (2026-11-25, updated with O1c)

| Number | Migration | Where | State |
| --- | --- | --- | --- |
| 0001 | `baseline_schema` | `main` | released |
| 0002 | `server_identity` | `main` | released |
| 0003 | `outbox_v2_consumers` | `main` | released |
| 0004 | `inventory_and_costing` | `main` | released |
| 0005 | `device_journal_register_series` | `main` (PR #49) | released |
| 0006 | `offline_sync` | PR #53 (O1b) | open |
| 0007 | `offline_limit` | PR O1c (stacked on #53) | open |
| 0008 | `payments_c3` | PR #26 (stacked on O1c) | open |

When a PR merges, update this table in the same PR.
