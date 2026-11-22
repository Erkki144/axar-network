import pytest
from sqlalchemy import func, select

from axar_pos.models import AppendOnlyViolation, OutboxEvent, Sale, SaleLine


def count(app, model):
    with app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_health_and_ui(client):
    assert client.get("/health").json()["payments"] == "simulated-cash-only"
    r = client.get("/")
    assert r.status_code == 200
    assert "AXAR POS" in r.text and 'lang="et"' in r.text


def test_sale_requires_open_drawer(a):
    r = a.sell([("KOHV", 1)], 250)
    assert r.status_code == 409
    assert r.json()["error"] == "no_open_drawer"


def test_cash_sale_server_computes_totals_vat_rounding_and_change(a):
    a.open_drawer()
    # 2 x 2.50 + 1 x 1.99 = 6.99 -> cash due 7.00 (5-cent rounding)
    r = a.sell([("KOHV", 2), ("SAI", 1)], 1000)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["receipt_no"] == 1
    assert s["total_cents"] == 699
    assert s["cash_rounding_cents"] == 1
    assert s["cash_due_cents"] == 700
    assert s["change_cents"] == 300
    # line VAT @ 24 %: 500 -> 97 (96.77), 199 -> 39 (38.52)
    assert [ln["vat_cents"] for ln in s["lines"]] == [97, 39]
    assert s["vat_cents"] == 136
    assert s["net_cents"] == 699 - 136
    assert all(ln["vat_rate_bp"] == 2400 for ln in s["lines"])
    assert s["simulated"] is True


def test_mixed_vat_rates(a):
    a.open_drawer()
    s = a.sell([("KOHV", 1), ("RAAMAT", 1)], 2000).json()
    assert [(ln["vat_rate_bp"], ln["vat_cents"]) for ln in s["lines"]] == [(2400, 48), (900, 132)]


def test_historic_vat_rate_applies_by_business_date(a, clock):
    from datetime import datetime, timezone

    clock.at = datetime(2025, 6, 30, 12, 0, tzinfo=timezone.utc)
    a.open_drawer()
    s = a.sell([("KOHV", 1)], 250).json()
    assert s["lines"][0]["vat_rate_bp"] == 2200
    assert s["lines"][0]["vat_cents"] == 45  # 250*22/122 = 45.08


def test_insufficient_tender_rejected_and_nothing_written(a, app):
    a.open_drawer()
    r = a.sell([("KOHV", 2), ("SAI", 1)], 695)  # due is 7.00
    assert r.status_code == 422
    assert r.json()["error"] == "insufficient_tender"
    assert count(app, Sale) == 0
    assert count(app, OutboxEvent) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"lines": [{"product_id": 1, "quantity": 1}], "tendered_cents": 2.5},
        {"lines": [{"product_id": 1, "quantity": 1}], "tendered_cents": "250"},
        {"lines": [{"product_id": 1, "quantity": 0}], "tendered_cents": 250},
        {"lines": [{"product_id": 1, "quantity": 1.5}], "tendered_cents": 250},
        {"lines": [], "tendered_cents": 250},
        {"lines": [{"product_id": 1, "quantity": 1}], "tendered_cents": -1},
        # client may not dictate prices or totals
        {"lines": [{"product_id": 1, "quantity": 1, "price_cents": 1}], "tendered_cents": 250},
        {"lines": [{"product_id": 1, "quantity": 1}], "tendered_cents": 250, "total_cents": 1},
    ],
)
def test_rejects_floats_strings_and_client_prices(a, body):
    a.open_drawer()
    r = a.post("/api/v1/sales/cash", body, headers={"Idempotency-Key": "k-validation-1"})
    assert r.status_code == 422


def test_idempotency_key_required(a):
    a.open_drawer()
    r = a.post("/api/v1/sales/cash", {"lines": [{"product_id": 1, "quantity": 1}],
                                       "tendered_cents": 250})
    assert r.status_code == 422


def test_retry_with_same_key_returns_original_sale(a, app):
    a.open_drawer()
    first = a.sell([("KOHV", 1)], 500, key="retry-key-0001")
    again = a.sell([("KOHV", 1)], 500, key="retry-key-0001")
    assert first.status_code == 201 and again.status_code == 200
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert count(app, Sale) == 1
    assert count(app, OutboxEvent) == 1


def test_same_key_different_body_is_conflict(a, app):
    a.open_drawer()
    a.sell([("KOHV", 1)], 500, key="reused-key-0001")
    r = a.sell([("KOHV", 2)], 500, key="reused-key-0001")
    assert r.status_code == 409
    assert r.json()["error"] == "idempotency_key_reused"
    assert count(app, Sale) == 1


def test_receipt_numbers_are_sequential(a):
    a.open_drawer()
    nos = [a.sell([("KOHV", 1)], 250).json()["receipt_no"] for _ in range(3)]
    assert nos == [1, 2, 3]


def test_receipt_text(a):
    a.open_drawer()
    sale = a.sell([("KOHV", 2), ("SAI", 1)], 1000).json()
    r = a.get(f"/api/v1/sales/{sale['id']}/receipt.txt")
    assert r.status_code == 200
    t = r.text
    assert "Kohvik A" in t and "SIMULEERITUD" in t
    assert "Tšekk nr 1" in t
    assert "10.03.2026 12:00" in t  # Tallinn local time
    assert "6,99 €" in t and "Ümardus" in t and "7,00 €" in t and "3,00 €" in t
    assert "KM 24%" in t


def test_outbox_event_written_with_hash(a, app):
    import hashlib
    import json

    a.open_drawer()
    sale = a.sell([("KOHV", 1)], 250).json()
    with app.state.session_factory() as s:
        ev = s.scalar(select(OutboxEvent))
    assert ev.type == "pos.sale.completed" and ev.version == 1
    assert ev.tenant_id == "t-a"
    assert ev.correlation_id == sale["correlation_id"]
    assert hashlib.sha256(ev.payload.encode()).hexdigest() == ev.payload_hash
    assert json.loads(ev.payload)["total_cents"] == 250


def test_sale_records_are_append_only(a, app):
    a.open_drawer()
    a.sell([("KOHV", 1)], 250)
    with app.state.session_factory() as s:
        sale = s.scalar(select(Sale))
        sale.total_cents = 1
        with pytest.raises(AppendOnlyViolation):
            s.flush()
        s.rollback()
        line = s.scalar(select(SaleLine))
        s.delete(line)
        with pytest.raises(AppendOnlyViolation):
            s.flush()


def test_price_change_does_not_alter_past_sale(a, app):
    from axar_pos.models import Product

    a.open_drawer()
    sale = a.sell([("KOHV", 1)], 250).json()
    with app.state.session_factory.begin() as s:
        s.scalar(select(Product).where(Product.sku == "KOHV", Product.tenant_id == "t-a")).price_cents = 999
    again = a.get(f"/api/v1/sales/{sale['id']}").json()
    assert again["lines"][0]["unit_price_cents"] == 250
    assert again["total_cents"] == 250


def test_concurrent_retries_with_same_key_create_one_sale(a, app):
    """Double-tap / network retry storm: N parallel requests, one key."""
    from concurrent.futures import ThreadPoolExecutor

    a.open_drawer()
    pid = a.product_id("KOHV")
    body = {"lines": [{"product_id": pid, "quantity": 1}], "tendered_cents": 250}

    def fire(_):
        return a.post("/api/v1/sales/cash", body, headers={"Idempotency-Key": "storm-key-0001"})

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fire, range(16)))

    assert all(r.status_code in (200, 201) for r in results), [r.text for r in results]
    assert sum(r.status_code == 201 for r in results) == 1
    assert len({r.json()["id"] for r in results}) == 1
    assert count(app, Sale) == 1
    assert count(app, OutboxEvent) == 1
