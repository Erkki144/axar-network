# Slice 5 — PostgreSQL, migrations, row-level security (issue #2, part 1)

Status: **local / CI only.** Branch `claude/axar-pos-s7-postgres-0r9ip8`,
stacked on PR #13. Nothing is provisioned or deployed. Identity is still the
demo header mode; server-side authentication is the next slice.

## What changed

| Area | Change |
| --- | --- |
| Migrations | Alembic (`axar_pos/migrations`, `alembic.ini`). `0001` = baseline of the slice 4 schema + RLS + grants. PostgreSQL runs `upgrade head` at start-up with the **owner** connection; SQLite (local demo, fast tests) keeps `create_all`. |
| Roles | Two connections: `AXAR_POS_ADMIN_DATABASE_URL` (owner: migrations, seeding, admin CLI) and `AXAR_POS_DATABASE_URL` (requests, role `axar_app`: not owner, not superuser, no `BYPASSRLS`). |
| RLS | Every tenant table: `ENABLE ROW LEVEL SECURITY` + policy `tenant_id = current_setting('app.tenant_id', true)` for reads **and** writes. Each request transaction starts with `set_config('app.tenant_id', …, true)` (= `SET LOCAL`), set by `db.bind_tenant` and an `after_begin` hook. Tenant not set → no rows (fail closed). |
| Least privilege | `axar_app` may only `SELECT, INSERT` the append-only tables (`sales`, `sale_lines`, `refunds`, `refund_lines`, `outbox_events`, `kitchen_ticket_lines`) and only `SELECT` `tenants`, `tax_rates`. The ORM append-only guard now has a database wall behind it. |
| Numbering | Receipt, refund and kitchen-ticket numbers come from `tenant_counters` via one `INSERT … ON CONFLICT DO UPDATE … RETURNING`. Unique and gap-free (a failed transaction rolls its number back). The first use seeds from existing `max()`, so old databases continue their numbering. Lock order: drawer row → counter row. |
| Refund concurrency | Refunds of the same sale are serialized with `SELECT … FOR UPDATE` on `sale_refund_state`. **Bug found by the PostgreSQL run:** before, parallel partial refunds of one line lost the compare-and-swap and got a false `over_refund` although units were left. The CAS guards stay as a second check. |
| Types | Money columns `BIGINT`; timestamps `timestamptz` on PostgreSQL (still aware-UTC in Python on both backends). `kitchen_ticket_lines.tenant_id` added (needed for RLS). |
| CI | New `test-postgres` job: whole suite on a `postgres:16` service + PostgreSQL-only tests, which must not be skipped. |

## PostgreSQL-only tests (`tests/test_postgres.py`)

- the app role is not owner/superuser and cannot bypass RLS;
- raw SQL without a `WHERE` sees only the bound tenant; unbound sees nothing;
- inserting a row for another tenant is rejected by RLS;
- `UPDATE`/`DELETE` on append-only tables, `tenants`, `tax_rates` → permission denied;
- no drift between the migration and the models (`alembic compare_metadata`);
- all `*_at` columns are `timestamptz`;
- **real lock interleaving:** a sale has claimed the drawer but not committed;
  a close on another connection blocks, then includes the sale (variance 0);
- a sale after a committed close → `no_open_drawer`;
- 40 parallel sales on two registers → receipt numbers exactly 1…40;
- a sale failing after it took a number does not burn the number;
- 8 parallel one-unit refunds of a 3-unit line → exactly 3 succeed, the rest
  are real over-refunds, Σ cash = sale's cash due;
- the same idempotency key in parallel → one sale;
- a lock timeout fails loudly.

The existing 165 tests also run on PostgreSQL (as `axar_app`, under RLS).

## Operating notes

- Create the app role yourself (it needs a password, which never goes in the
  repo): `CREATE ROLE axar_app LOGIN PASSWORD '…';` then run the app; the
  migration grants privileges if the role exists.
- Migrate explicitly: `AXAR_POS_ADMIN_DATABASE_URL=… alembic upgrade head`.
- Every later schema change is a new Alembic revision that also extends the
  RLS / grant lists for new tables. Data migrations need human review.
