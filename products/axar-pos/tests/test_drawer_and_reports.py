from datetime import datetime, timezone

from axar_pos.config import Settings
from axar_pos.services import business_date_for


def test_cannot_open_two_drawers_on_same_register(a):
    a.open_drawer()
    r = a.post("/api/v1/drawer/open", {"register_id": a.register_id("R1"),
                                       "opening_float_cents": 0})
    assert r.status_code == 409
    assert r.json()["error"] == "drawer_already_open"


def test_drawer_reconciliation_balanced(a):
    d = a.open_drawer(5000)
    a.sell([("KOHV", 2), ("SAI", 1)], 1000)   # cash due 7.00
    a.sell([("KOHV", 1)], 250)                 # 2.50
    cur = a.drawer()
    assert cur["expected_cents"] == 5000 + 700 + 250
    assert cur["sale_count"] == 2

    r = a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5950})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["status"] == "closed"
    assert c["cash_sales_cents"] == 950
    assert c["expected_cents"] == 5950
    assert c["variance_cents"] == 0


def test_drawer_reconciliation_reports_shortage(a):
    d = a.open_drawer(5000)
    a.sell([("KOHV", 1)], 250)
    c = a.post(f"/api/v1/drawer/{d['id']}/close",
               {"counted_cents": 5200, "note": "puudu 0,50"}).json()
    assert c["expected_cents"] == 5250
    assert c["variance_cents"] == -50
    assert c["note"] == "puudu 0,50"


def test_closed_drawer_blocks_sales_and_double_close(a):
    d = a.open_drawer()
    a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5000})
    assert a.sell([("KOHV", 1)], 250).json()["error"] == "no_open_drawer"
    r = a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5000})
    assert r.status_code == 409


def test_daily_summary(a):
    a.open_drawer()
    a.sell([("KOHV", 2), ("SAI", 1)], 1000)
    a.sell([("RAAMAT", 1)], 1600)
    r = a.get("/api/v1/reports/daily", params={"business_date": "2026-03-10"}).json()
    assert r["sale_count"] == 2
    assert r["total_cents"] == 699 + 1599
    assert r["vat_cents"] == 136 + 132
    assert r["net_cents"] == r["total_cents"] - r["vat_cents"]
    assert r["cash_rounding_cents"] == 1 + 1          # 6.99->7.00, 15.99->16.00
    assert r["cash_collected_cents"] == 700 + 1600
    assert (r["first_receipt_no"], r["last_receipt_no"]) == (1, 2)
    assert r["vat_breakdown"] == [
        {"vat_rate_bp": 900, "gross_cents": 1599, "vat_cents": 132, "net_cents": 1467},
        {"vat_rate_bp": 2400, "gross_cents": 699, "vat_cents": 136, "net_cents": 563},
    ]
    # Sum of breakdown reconciles to totals.
    assert sum(x["gross_cents"] for x in r["vat_breakdown"]) == r["total_cents"]


def test_business_day_uses_tallinn_time_not_utc():
    s = Settings()
    # 23:30 UTC on 9 March == 01:30 on 10 March in Tallinn
    assert business_date_for(datetime(2026, 3, 9, 23, 30, tzinfo=timezone.utc), s).isoformat() == "2026-03-10"
    # Summer time (EEST, UTC+3): 21:30 UTC 1 July == 00:30 2 July
    assert business_date_for(datetime(2026, 7, 1, 21, 30, tzinfo=timezone.utc), s).isoformat() == "2026-07-02"


def test_business_day_cutoff_hour():
    s = Settings(business_day_cutoff_hour=5)
    # 03:00 Tallinn on 10 March still belongs to 9 March
    assert business_date_for(datetime(2026, 3, 10, 1, 0, tzinfo=timezone.utc), s).isoformat() == "2026-03-09"
    # 05:00 Tallinn starts 10 March
    assert business_date_for(datetime(2026, 3, 10, 3, 0, tzinfo=timezone.utc), s).isoformat() == "2026-03-10"


def test_late_night_sale_lands_on_local_day(a, clock):
    clock.at = datetime(2026, 3, 9, 23, 30, tzinfo=timezone.utc)
    a.open_drawer()
    s = a.sell([("KOHV", 1)], 250).json()
    assert s["business_date"] == "2026-03-10"
    assert a.get("/api/v1/reports/daily", params={"business_date": "2026-03-09"}).json()["sale_count"] == 0
