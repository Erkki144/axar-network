"""P1 review items on PR #7: per-register drawers and sale-vs-close races."""

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, update

from axar_pos import services
from axar_pos.models import DrawerSession, Sale


# --------------------------------------------------------------- multi-register


def test_two_registers_have_independent_open_drawers(a):
    d1 = a.open_drawer(5000, register="R1")
    d2 = a.open_drawer(10000, register="R2")
    assert d1["id"] != d2["id"]
    assert (d1["register_id"], d2["register_id"]) == (a.register_id("R1"), a.register_id("R2"))

    a.sell([("KOHV", 1)], 250, register="R1")
    a.sell([("KOHV", 2), ("SAI", 1)], 1000, register="R2")   # due 7.00
    a.sell([("SAI", 1)], 200, register="R2")                 # 1.99 -> 2.00

    assert a.drawer("R1")["expected_cents"] == 5000 + 250
    assert a.drawer("R2")["expected_cents"] == 10000 + 700 + 200

    c1 = a.post(f"/api/v1/drawer/{d1['id']}/close", {"counted_cents": 5250}).json()
    assert (c1["expected_cents"], c1["variance_cents"], c1["sale_count"]) == (5250, 0, 1)

    # R2 keeps selling after R1 closed.
    assert a.sell([("KOHV", 1)], 250, register="R2").status_code == 201
    assert a.sell([("KOHV", 1)], 250, register="R1").json()["error"] == "no_open_drawer"

    c2 = a.post(f"/api/v1/drawer/{d2['id']}/close", {"counted_cents": 10900}).json()
    assert c2["expected_cents"] == 10000 + 700 + 200 + 250
    assert c2["variance_cents"] == -250
    assert c2["sale_count"] == 3


def test_daily_report_breaks_down_by_register(a):
    a.open_drawer(register="R1")
    a.open_drawer(register="R2")
    a.sell([("KOHV", 1)], 250, register="R1")
    a.sell([("KOHV", 2)], 500, register="R2")
    r = a.get("/api/v1/reports/daily", params={"business_date": "2026-03-10"}).json()
    assert r["by_register"] == [
        {"register_id": a.register_id("R1"), "sale_count": 1, "total_cents": 250,
         "cash_collected_cents": 250},
        {"register_id": a.register_id("R2"), "sale_count": 1, "total_cents": 500,
         "cash_collected_cents": 500},
    ]


def test_receipt_shows_location_and_register(a):
    a.open_drawer(register="R2")
    sale = a.sell([("KOHV", 1)], 250, register="R2").json()
    assert "Kohvik A kesklinn · Kassa 2" in a.get(f"/api/v1/sales/{sale['id']}/receipt.txt").text


def test_idempotency_key_bound_to_register(a):
    a.open_drawer(register="R1")
    a.open_drawer(register="R2")
    a.sell([("KOHV", 1)], 250, key="reg-bound-0001", register="R1")
    r = a.sell([("KOHV", 1)], 250, key="reg-bound-0001", register="R2")
    assert r.status_code == 409
    assert r.json()["error"] == "idempotency_key_reused"


# ------------------------------------------------------------- sale vs close race


def test_stale_open_drawer_read_cannot_commit_into_closed_drawer(a, app, monkeypatch):
    """Deterministic interleaving: the sale reads 'open', then the drawer is
    closed, then the sale tries to commit. The conditional claim must reject
    it. This exercises the guard that protects PostgreSQL, independent of
    SQLite's write serialization."""
    d = a.open_drawer(5000)
    a.sell([("KOHV", 1)], 250)

    real_get_open = services.get_open_drawer
    closed = threading.Event()

    def stale_get_open(s, tenant_id, register_id):
        drawer = real_get_open(s, tenant_id, register_id)
        drawer_id = drawer.id
        # Simulate a close committing right after our read. On SQLite a second
        # connection cannot do this (BEGIN IMMEDIATE holds the write lock —
        # which is itself the SQLite protection), so the close is committed
        # through this same session; the sale's claim then runs in a fresh
        # transaction against the closed row, exactly as on PostgreSQL.
        services.close_drawer(s, tenant_id, drawer_id, "manager", 5250, None,
                              app.state.settings.clock())
        closed.set()
        stale = DrawerSession(id=drawer_id, tenant_id=tenant_id, register_id=register_id,
                              status="open")  # detached snapshot of what we read
        return stale

    body = a.sale_body([("KOHV", 2)], 500)  # resolve ids before patching
    monkeypatch.setattr(services, "get_open_drawer", stale_get_open)
    r = a.post("/api/v1/sales/cash", body, headers={"Idempotency-Key": "stale-read-0001"})
    monkeypatch.setattr(services, "get_open_drawer", real_get_open)

    assert closed.is_set()
    assert r.status_code == 409 and r.json()["error"] == "no_open_drawer"
    final = a.get(f"/api/v1/drawer/{d['id']}").json()
    assert final["status"] == "closed"
    assert (final["sale_count"], final["cash_sales_cents"], final["expected_cents"]) == (1, 250, 5250)
    with app.state.session_factory() as s:
        assert len(s.scalars(select(Sale)).all()) == 1  # nothing written


@pytest.mark.parametrize("round_", range(5))
def test_parallel_sales_vs_close_keep_drawer_consistent(a, app, round_):
    d = a.open_drawer(5000)
    body = a.sale_body([("KOHV", 1)], 250)
    start = threading.Barrier(13)

    def sell(_):
        start.wait()
        return a.post("/api/v1/sales/cash", body, headers={"Idempotency-Key": str(uuid.uuid4())})

    def close():
        start.wait()
        time.sleep(0.005 * round_)  # vary where the close lands
        return a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 0})

    with ThreadPoolExecutor(max_workers=13) as pool:
        sales = [pool.submit(sell, i) for i in range(12)]
        closing = pool.submit(close)
        results = [f.result() for f in sales]
        close_r = closing.result()

    assert close_r.status_code == 200, close_r.text
    ok = [r for r in results if r.status_code == 201]
    rejected = [r for r in results if r.status_code != 201]
    assert all(r.status_code == 409 and r.json()["error"] == "no_open_drawer" for r in rejected)

    closed = close_r.json()
    # Every accepted sale is counted in the closed drawer, nothing else is.
    assert closed["sale_count"] == len(ok)
    assert closed["cash_sales_cents"] == 250 * len(ok)
    assert closed["expected_cents"] == 5000 + 250 * len(ok)
    with app.state.session_factory() as s:
        ledger = s.scalars(select(Sale).where(Sale.drawer_session_id == d["id"])).all()
        assert len(ledger) == len(ok)
        closed_at = s.get(DrawerSession, d["id"]).closed_at
        assert all(x.created_at <= closed_at for x in ledger)


def test_close_refuses_when_running_total_disagrees_with_ledger(a, app):
    d = a.open_drawer(5000)
    a.sell([("KOHV", 1)], 250)
    with app.state.session_factory.begin() as s:  # simulate corruption
        s.execute(update(DrawerSession).where(DrawerSession.id == d["id"])
                  .values(cash_sales_cents=999))
    r = a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5250})
    assert r.status_code == 500 and r.json()["error"] == "ledger_mismatch"
    assert a.drawer()["status"] == "open"  # rolled back, needs a human


# --------------------------------------------------------------------- timezone


def test_timestamps_round_trip_as_aware_utc(a, app):
    a.open_drawer()
    sale_id = a.sell([("KOHV", 1)], 250).json()["id"]
    with app.state.session_factory() as s:
        sale = s.get(Sale, sale_id)
    assert sale.created_at.utcoffset().total_seconds() == 0
    assert a.get(f"/api/v1/sales/{sale_id}").json()["created_at"].endswith("+00:00")


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs POSIX tzset")
def test_receipt_time_independent_of_server_timezone(a):
    a.open_drawer()
    sale_id = a.sell([("KOHV", 1)], 250).json()["id"]
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        text = a.get(f"/api/v1/sales/{sale_id}/receipt.txt").text
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()
    assert "10.03.2026 12:00" in text  # Tallinn time, not New York / UTC
