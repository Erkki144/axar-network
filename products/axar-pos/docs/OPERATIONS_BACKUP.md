# Backup, restore and restore drill (SEC-2, #34)

Status: **tooling + automated drill test exist. There is no production schedule yet**, because the central server has no hosting yet (audit B2). This page states only what is tested and what is not.

## Tool

`python -m axar_pos.backup {dump|restore|verify}` uses the owner connection from `AXAR_POS_ADMIN_DATABASE_URL`. Credentials go to `pg_dump`/`pg_restore` through libpq environment variables, never as command-line arguments.

| Step | What it guarantees |
| --- | --- |
| `dump --out X.dump` | **The dump:** `pg_dump` custom format, including RLS policies and grants. **Sidecar files:** `X.dump.sha256`, and `X.dump.manifest.json` (alembic revision + row count + content md5 of every table). **Consistency check:** the backup is refused if the content changed during the dump. |
| `restore --dump X.dump` | Refuses a dump whose checksum does not match. Refuses a target that is not empty. Refuses a server without the `axar_app` role (roles are per server; create it first). Restores in **one transaction**: all or nothing. |
| (all) | **Client tools must match the server's major version.** A newer `pg_dump` writes SQL an older server rejects. This happened in CI: `pg_dump` 17+ wrote `SET transaction_timeout`, which PostgreSQL 16 refused. The tool searches `$AXAR_POS_PG_BIN`, then `/usr/lib/postgresql/<server major>/bin`, then `PATH`. If none matches, it refuses with a clear error. |
| `verify --dump X.dump` | Compares every table's row count and content hash, plus the alembic revision. Exit code 0 only if they are identical. |

## Tested (CI job `test-postgres`, `tests/test_backup_restore.py`)

| Test | Proves |
| --- | --- |
| `test_backup_restores_identical_content_rls_grants_and_keeps_selling` | **Data:** a business day (4 sales, a refund, a table bill through the kitchen, a stock invoice, worker consumption) restores into a new database with **every table identical**. **RLS:** without a tenant the app role sees 0 sales; with tenant `t-a` it sees 4. **Selling continues:** on the restored copy the till sells with receipt number = last + 1. **Idempotency:** an original key replays the original sale; it does not sell twice. |
| `test_corrupted_dump_and_non_empty_target_are_refused` | One flipped byte → refused, and nothing is written. A non-empty target → refused. |
| `test_verify_detects_a_changed_restore` | A one-cent price change in the restored copy is reported by `verify`. |
| `test_credentials_never_go_on_the_command_line` | The password goes only into `PGPASSWORD` (environment), not into argv. |

**Mutation check (run locally, 2026-11-25).** Each mutant makes the relevant test fail:
- restore with `--no-privileges` (grants lost);
- skipping the checksum check;
- a fingerprint that counts rows only, without content.

**CLI run (local PostgreSQL 16):** dump 0.54 s, restore 0.65 s, `verify OK`. A second restore into the same, now non-empty, database is refused with exit code 1. These timings are for a nearly empty database and say **nothing** about pilot-sized data.

## RPO / RTO — honest statement

| | With this tool alone | Target for the pilot (needs hosting decision B2) |
| --- | --- | --- |
| RPO (data that can be lost) | up to the interval between dumps (e.g. **24 h** with a nightly dump) | **≤ 5 min**: managed PostgreSQL with continuous WAL archiving / point-in-time recovery, **plus** offline-first tills that keep unsynced sales locally (ADR-0012), so a central loss does not lose sales made at the till |
| RTO (time to serve again) | restore time + app start; **not measured at pilot data size** | **≤ 1 h** for central; tills keep selling offline meanwhile (ADR-0012) |

## Runbook

1. **Prepare the target server (once):**
   - create the roles: the owner role, and `axar_app` with LOGIN and its password from the secret store;
   - create an empty database.
2. **Restore:** run `AXAR_POS_ADMIN_DATABASE_URL=<owner URL of the empty DB> python -m axar_pos.backup restore --dump X.dump`.
3. **Verify:** run `python -m axar_pos.backup verify --dump X.dump`. It must print `verify OK`.
4. **Start the app** against the restored database, using its `axar_app` URL.
5. **Smoke test:** open a drawer on a test register, make one sale, and check that the receipt number is the next one.
6. **Record the drill:** date, dump file, sizes, restore duration, `verify` output, and the operator.

**Drill schedule:** monthly, and after every schema migration. The automated test runs on every PR.

## Not covered (gaps with owners)

- **Encryption at rest and off-site copies.** The dump files are not encrypted by this tool. Storage must encrypt them (managed backup storage or `age`/`gpg` with keys in a KMS); keep **separate copies in another region**. Owner: hosting decision B2.
- **Point-in-time recovery / WAL archiving.** Use managed PostgreSQL, or configure `archive_command`. Not implemented here.
- **Edge (till) SQLite backups.** These belong to ADR-0012 stage O5.
- **Restoring a single tenant out of a multi-tenant dump.** Not supported. The restore is for the whole database.
