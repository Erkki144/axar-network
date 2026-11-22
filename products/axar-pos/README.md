# AXAR POS — isolated product bootstrap

Status: **Slice 1 / local demo / simulated cash only / not production ready**.

AXAR POS is a separate future product from the existing AXAR Network content-marketing agents. It lives entirely under `products/axar-pos/` and does not touch the Next.js app.

## What runs today (Slice 1)

- Estonian touchscreen cash-sale UI at `/`
- FastAPI + SQLAlchemy; server-owned prices, integer-cent money, versioned VAT, 5-cent cash rounding
- Idempotent cash sales, sequential receipts, immutable sale snapshots, transactional outbox event
- Tenant → location → register → drawer session; independent drawers per register
- Cash drawer open/close with expected vs counted variance, race-safe against concurrent sales
- Europe/Tallinn business-day report with VAT breakdown
- Tenant-scoped queries with cross-tenant tests; demo identity must be switched on explicitly

Details, tenant-boundary plan, limitations and rollback: [`docs/SLICE_1.md`](docs/SLICE_1.md). Rules for coding agents: [`AGENTS.md`](AGENTS.md).

The earlier Sprint 0 ZIP (`axar-pos-sprint0.zip`) is **not** in this repository; Slice 1 was written from the architecture docs.

## Run tests

```bash
cd products/axar-pos
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
```

## Run the local demo

```bash
cd products/axar-pos
AXAR_POS_DEMO_IDENTITY=1 AXAR_POS_SEED_DEMO=1 uvicorn axar_pos.main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000/
```

`AXAR_POS_DEMO_IDENTITY=1` trusts `X-Tenant-ID` / `X-Cashier-ID` headers with **no authentication**. Never bind to a public interface.

| Env var | Default | Meaning |
| --- | --- | --- |
| `AXAR_POS_DATABASE_URL` | `sqlite:///./axar_pos.db` | SQLAlchemy URL |
| `AXAR_POS_TIMEZONE` | `Europe/Tallinn` | business-day timezone |
| `AXAR_POS_DAY_CUTOFF_HOUR` | `0` | local hour a business day starts |
| `AXAR_POS_CASH_ROUNDING` | `5` | cash rounding step in cents (`1` disables) |
| `AXAR_POS_DEMO_IDENTITY` | off | enable unauthenticated header identity |
| `AXAR_POS_SEED_DEMO` | off | seed tenant `demo` with sample products |

## API (v1)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | no identity required |
| GET | `/api/v1/products` | tenant catalog |
| GET | `/api/v1/registers` | registers with location and open drawer |
| GET | `/api/v1/registers/{id}/drawer` | open drawer with running expected cash |
| GET | `/api/v1/drawer/{id}` | drawer session (open or closed) |
| POST | `/api/v1/drawer/open` | `{register_id, opening_float_cents}` |
| POST | `/api/v1/drawer/{id}/close` | `{counted_cents, note?}` → variance; refuses on ledger mismatch |
| POST | `/api/v1/sales/cash` | header `Idempotency-Key`; `{register_id, lines:[{product_id, quantity}], tendered_cents}` |
| GET | `/api/v1/sales[?business_date=]` | |
| GET | `/api/v1/sales/{id}` | |
| GET | `/api/v1/sales/{id}/receipt.txt` | Estonian plain-text receipt |
| GET | `/api/v1/reports/daily[?business_date=]` | |

## Next milestones

See issues #1–#5. Next up: #2 (auth, roles, PostgreSQL/Alembic, RLS) and #3 (refunds as compensating records, settlement reconciliation).

See `docs/ARCHITECTURE.md` and `docs/BUILD_HANDOFF.md`.
