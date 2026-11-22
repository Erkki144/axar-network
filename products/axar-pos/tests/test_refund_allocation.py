"""Regression tests for AION review on PR #7 (2026-11-22):

P0  partial refunds dumped the line's remaining VAT on the last refund
    (1 cent x 100, refunded unit by unit -> last refund gross 1, VAT 19, net -18).
P1  cash rounding applied per refund drifted from the cash the customer paid
    (696 -> paid 695; 3 x 232 refunded separately -> 690).

Invariants checked for every refund and every split:
  0 <= vat <= gross, net >= 0 (per refund and per refund line)
  after a full refund: Σ gross == line gross, Σ VAT == line VAT (per line),
  Σ cash paid == sale cash_due (per sale), regardless of split and order.
"""

import random
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from axar_pos.models import Product, Refund


PRODUCTS = [  # sku, price, tax category
    ("CENT", 1, "standard"),
    ("SEVEN", 7, "standard"),
    ("P199", 199, "standard"),
    ("P232", 232, "standard"),
    ("P1599", 1599, "reduced"),
    ("P333", 333, "zero"),
]


@pytest.fixture
def m(a, app):
    with app.state.session_factory.begin() as s:
        for sku, price, cat in PRODUCTS:
            s.add(Product(tenant_id="t-a", sku=sku, name=sku, price_cents=price, tax_category=cat))
    a.open_drawer(10_000_000)
    return a.as_role("manager")


def check_refund(rf):
    assert 0 <= rf["vat_cents"] <= rf["total_cents"], rf
    assert rf["net_cents"] >= 0, rf
    for ln in rf["lines"]:
        assert 0 <= ln["vat_cents"] <= ln["gross_cents"], ln
        assert ln["net_cents"] >= 0, ln
    assert -4 <= rf["cash_rounding_cents"] <= 4
    assert rf["cash_paid_cents"] >= 0


def assert_fully_reversed(a, sale, refunds):
    by_line = {ln["line_no"]: [0, 0] for ln in sale["lines"]}
    sale_line_by_id = {}
    for rf in refunds:
        for ln in rf["lines"]:
            sale_line_by_id.setdefault(ln["sale_line_id"], ln["sku"])
    # Map refund lines back to original line_no via sku (unique per sale here).
    sku_to_no = {ln["sku"]: ln["line_no"] for ln in sale["lines"]}
    for rf in refunds:
        for ln in rf["lines"]:
            acc = by_line[sku_to_no[ln["sku"]]]
            acc[0] += ln["gross_cents"]
            acc[1] += ln["vat_cents"]
    for ln in sale["lines"]:
        assert by_line[ln["line_no"]] == [ln["gross_cents"], ln["vat_cents"]], ln["sku"]
    assert sum(r["total_cents"] for r in refunds) == sale["total_cents"]
    assert sum(r["vat_cents"] for r in refunds) == sale["vat_cents"]
    assert sum(r["cash_paid_cents"] for r in refunds) == sale["cash_due_cents"]
    after = a.get(f"/api/v1/sales/{sale['id']}").json()
    assert all(x["refunded_quantity"] == x["quantity"] for x in after["lines"])


# --------------------------------------------------------------- AION P0 / P1


def test_p0_hundred_one_cent_units_refunded_one_by_one(a, m):
    sale = a.sell([("CENT", 100)], 100).json()
    assert (sale["total_cents"], sale["vat_cents"], sale["cash_due_cents"]) == (100, 19, 100)
    refunds = []
    for _ in range(100):
        r = m.refund(sale["id"], [(1, 1)])
        assert r.status_code == 201, r.text
        check_refund(r.json())
        refunds.append(r.json())
    assert max(r["vat_cents"] for r in refunds) == 1
    assert_fully_reversed(a, sale, refunds)


def test_p1_cash_rounding_is_cumulative_per_sale(a, m):
    sale = a.sell([("P232", 3)], 1000).json()
    assert (sale["total_cents"], sale["cash_due_cents"]) == (696, 695)
    paid = []
    for _ in range(3):
        rf = m.refund(sale["id"], [(1, 1)]).json()
        check_refund(rf)
        paid.append(rf["cash_paid_cents"])
    # round(232)=230, round(464)=465 (+235), round(696)=695 (+230)
    assert paid == [230, 235, 230]
    assert sum(paid) == sale["cash_due_cents"] == 695


def test_drawer_returns_exactly_to_float_after_split_full_refund(a, m):
    float_ = a.drawer()["expected_cents"]
    sale = a.sell([("P232", 3), ("SEVEN", 5)], 2000).json()
    for line_no, q in [(2, 2), (1, 1), (2, 3), (1, 2)]:
        check_refund(m.refund(sale["id"], [(line_no, q)]).json())
    assert a.drawer()["expected_cents"] == float_


# ------------------------------------------------------------ property tests


def _random_split(rng, sale):
    """Random sequence of refund requests that fully refunds the sale."""
    left = {ln["line_no"]: ln["quantity"] for ln in sale["lines"]}
    requests = []
    while any(left.values()):
        open_lines = [n for n, q in left.items() if q]
        chosen = rng.sample(open_lines, rng.randint(1, len(open_lines)))
        req = []
        for n in chosen:
            q = rng.randint(1, left[n])
            left[n] -= q
            req.append((n, q))
        requests.append(req)
    return requests


@pytest.mark.parametrize("seed", range(25))
def test_property_any_split_reverses_exactly(a, m, seed):
    rng = random.Random(seed)
    skus = rng.sample([p[0] for p in PRODUCTS], rng.randint(1, 4))
    lines = [(sku, rng.randint(1, 40)) for sku in skus]
    sale = a.sell(lines, 1_000_000).json()
    refunds = []
    for req in _random_split(rng, sale):
        r = m.refund(sale["id"], req)
        assert r.status_code == 201, (seed, req, r.text)
        check_refund(r.json())
        refunds.append(r.json())
    assert_fully_reversed(a, sale, refunds)


@pytest.mark.parametrize("seed", range(5))
def test_property_order_does_not_change_cash_total(a, m, seed):
    rng = random.Random(1000 + seed)
    lines = [("P232", 7), ("SEVEN", 9), ("P199", 3)]
    s1 = a.sell(lines, 1_000_000).json()
    s2 = a.sell(lines, 1_000_000).json()
    split = _random_split(rng, s1)
    r1 = [m.refund(s1["id"], req).json() for req in split]
    r2 = [m.refund(s2["id"], req).json() for req in reversed(split)]
    for rf in r1 + r2:
        check_refund(rf)
    assert sum(x["cash_paid_cents"] for x in r1) == sum(x["cash_paid_cents"] for x in r2) \
        == s1["cash_due_cents"]
    assert sum(x["vat_cents"] for x in r1) == sum(x["vat_cents"] for x in r2) == s1["vat_cents"]


def test_concurrent_unit_refunds_reverse_exactly(a, m, app):
    sale = a.sell([("CENT", 30), ("P232", 5)], 2000).json()
    reqs = [[(1, 1)]] * 30 + [[(2, 1)]] * 5

    def fire(req):
        while True:  # client retries a 409 from a lost compare-and-swap
            r = m.refund(sale["id"], req)
            if r.status_code == 201:
                return r.json()
            assert r.status_code == 409, r.text

    with ThreadPoolExecutor(max_workers=8) as pool:
        refunds = list(pool.map(fire, reqs))
    for rf in refunds:
        check_refund(rf)
    assert_fully_reversed(a, sale, refunds)
    with app.state.session_factory() as s:
        assert len(s.scalars(select(Refund)).all()) == 35
