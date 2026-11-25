"""PostgreSQL-only guarantees (RFC-0002 §6/§7). Skipped on SQLite.

Run: AXAR_TEST_PG=postgresql+psycopg://postgres@127.0.0.1:5432 pytest tests
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from axar_pos import services
from axar_pos.db import bind_tenant
from axar_pos.models import Sale

import os

PG_BASE = os.environ.get("AXAR_TEST_PG")

pytestmark = pytest.mark.skipif(not PG_BASE, reason="PostgreSQL only (set AXAR_TEST_PG)")


def app_conn(app, tenant=None):
    """Raw connection as the non-owner app role, optionally tenant-bound."""
    conn = app.state.engine.connect()
    if tenant:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
    return conn


# --------------------------------------------------------------- RLS + grants


def test_app_role_is_not_owner_and_cannot_bypass_rls(app):
    with app_conn(app) as c:
        row = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
        assert c.scalar(text("SELECT current_user")) == "axar_app"
        assert row == (False, False)
        owner = c.scalar(text("SELECT tableowner FROM pg_tables WHERE tablename = 'sales'"))
        assert owner != "axar_app"


def test_rls_hides_other_tenants_rows_even_without_where(a, b, app):
    a.open_drawer(); b.open_drawer()
    assert a.sell([("KOHV", 1)], 250).status_code == 201
    assert b.sell([("B-SECRET", 1)], 1000).status_code == 201
    with app_conn(app, "t-a") as c:
        assert {r for (r,) in c.execute(text("SELECT tenant_id FROM sales"))} == {"t-a"}
        assert {r for (r,) in c.execute(text("SELECT tenant_id FROM products"))} == {"t-a"}
    with app_conn(app) as c:  # tenant not set: fail closed, nothing visible
        assert c.scalar(text("SELECT count(*) FROM sales")) == 0
        assert c.scalar(text("SELECT count(*) FROM products")) == 0


def test_rls_rejects_writing_rows_for_another_tenant(app):
    with app_conn(app, "t-a") as c:
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(text("INSERT INTO dining_tables (tenant_id, location_id, code, name, active) "
                           "SELECT 't-b', id, 'X9', 'smuggled', true FROM locations LIMIT 1"))


def test_app_role_cannot_update_or_delete_append_only_tables(a, app):
    a.open_drawer()
    assert a.sell([("KOHV", 1)], 250).status_code == 201
    for sql in ("UPDATE sales SET total_cents = 1", "DELETE FROM sale_lines",
                "UPDATE outbox_events SET payload = '{}'", "DELETE FROM refunds",
                "UPDATE tenants SET name = 'x'", "DELETE FROM tax_rates",
                "UPDATE auth_events SET outcome = 'ok'", "DELETE FROM auth_events",
                "DELETE FROM auth_sessions", "DELETE FROM devices",
                "UPDATE stock_movements SET value_cents = 0", "DELETE FROM stock_movements",
                "DELETE FROM recipe_lines", "UPDATE recipes SET version = 9"):
        with app_conn(app, "t-a") as c:
            with pytest.raises(ProgrammingError, match="permission denied"):
                c.execute(text(sql))


def test_migrations_match_models_no_drift(app):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from axar_pos.models import Base

    with app.state.admin_engine.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diff == [], diff


def test_timestamps_are_timestamptz(app):
    with app.state.admin_engine.connect() as c:
        types = set(c.scalars(text(
            "SELECT DISTINCT data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name LIKE '%\\_at' ESCAPE '\\'")))
    assert types == {"timestamp with time zone"}


# ------------------------------------------- real two-connection interleavings


def test_close_waits_for_in_flight_sale_and_includes_it(a, app, settings):
    """Connection 1 has claimed the drawer but not committed; connection 2's
    close must block on the row lock and then count the sale."""
    d = a.open_drawer(5000)
    reg = a.register_id()
    pid = a.product_id("KOHV")
    in_tx, release = threading.Event(), threading.Event()

    def hold(_s, _sale):
        in_tx.set()
        assert release.wait(10)

    def sell():
        with app.state.request_factory() as s:
            bind_tenant(s, "t-a")
            return services.create_cash_sale(
                s, tenant_id="t-a", register_id=reg, cashier_id="k1", idempotency_key="pg-hold-0001",
                lines=[services.LineRequest(pid, 1)], tendered_cents=250, settings=settings,
                now=settings.clock(), before_commit=hold)

    with ThreadPoolExecutor(2) as pool:
        sale_f = pool.submit(sell)
        assert in_tx.wait(10)
        close_f = pool.submit(a.post, f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5250})
        time.sleep(0.5)
        assert not close_f.done()                      # blocked on the drawer row lock
        release.set()
        sale, _ = sale_f.result(10)
        closed = close_f.result(10)
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert (body["sale_count"], body["expected_cents"], body["variance_cents"]) == (1, 5250, 0)


def test_sale_after_close_committed_is_rejected(a, app):
    d = a.open_drawer(5000)
    assert a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5000}).status_code == 200
    r = a.sell([("KOHV", 1)], 250)
    assert r.status_code == 409 and r.json()["error"] == "no_open_drawer"


def test_receipt_numbers_unique_and_gap_free_under_parallel_load(a, app):
    a.open_drawer(register="R1"); a.open_drawer(register="R2")
    with ThreadPoolExecutor(16) as pool:
        rs = list(pool.map(lambda i: a.sell([("KOHV", 1)], 250, register="R1" if i % 2 else "R2"), range(40)))
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs if r.status_code != 201]
    with app.state.session_factory() as s:
        nos = sorted(s.scalars(select(Sale.receipt_no).where(Sale.tenant_id == "t-a")))
    assert nos == list(range(1, 41))


def test_failed_sale_does_not_burn_a_receipt_number(a, app, settings):
    a.open_drawer()
    assert a.sell([("KOHV", 1)], 250).json()["receipt_no"] == 1
    reg, pid = a.register_id(), a.product_id("KOHV")

    def boom(_s, _sale):
        raise services.Conflict("simulated failure after numbering")

    with app.state.request_factory() as s:
        bind_tenant(s, "t-a")
        with pytest.raises(services.Conflict):
            services.create_cash_sale(
                s, tenant_id="t-a", register_id=reg, cashier_id="k1", idempotency_key="pg-fail-0001",
                lines=[services.LineRequest(pid, 1)], tendered_cents=250, settings=settings,
                now=settings.clock(), before_commit=boom)
    assert a.sell([("KOHV", 1)], 250).json()["receipt_no"] == 2


def test_parallel_partial_refunds_of_one_line_never_exceed_quantity(a, app):
    a.open_drawer(50_000)
    sale = a.sell([("KOHV", 3)], 1000).json()
    m = a.as_role("manager")
    with ThreadPoolExecutor(8) as pool:
        rs = list(pool.map(lambda i: m.refund(sale["id"], [(1, 1)]), range(8)))
    ok = [r for r in rs if r.status_code == 201]
    assert len(ok) == 3
    # Losers are real over-refunds (nothing left), not concurrency artefacts.
    assert all(r.status_code == 409 and "refundable 0" in r.json()["message"] for r in rs if r.status_code != 201)
    assert sum(r.json()["cash_paid_cents"] for r in ok) == sale["cash_due_cents"]
    assert sorted(r.json()["refund_no"] for r in ok) == [1, 2, 3]


def test_same_idempotency_key_in_parallel_creates_one_sale(a, app):
    a.open_drawer()
    with ThreadPoolExecutor(8) as pool:
        rs = list(pool.map(lambda _: a.sell([("KOHV", 1)], 250, key="pg-same-key-01"), range(8)))
    assert {r.status_code for r in rs} <= {200, 201}
    assert len({r.json()["id"] for r in rs}) == 1
    with app.state.session_factory() as s:
        assert len(list(s.scalars(select(Sale.id)))) == 1


def test_lock_timeout_is_not_reported_as_success(a, app, settings):
    """A statement that cannot get the drawer lock fails loudly (no silent
    success); here forced with a 100 ms lock_timeout while another tx holds it."""
    d = a.open_drawer(5000)
    with app.state.request_factory() as s1:
        bind_tenant(s1, "t-a")
        s1.execute(text("SELECT 1 FROM drawer_sessions WHERE id = :i FOR UPDATE"), {"i": d["id"]})
        with app.state.request_factory() as s2:
            bind_tenant(s2, "t-a")
            s2.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                s2.execute(text("UPDATE drawer_sessions SET sale_count = sale_count WHERE id = :i"), {"i": d["id"]})
        s1.rollback()
