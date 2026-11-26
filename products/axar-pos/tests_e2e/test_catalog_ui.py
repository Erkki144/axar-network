"""P1-1a in the real browser against a real server (docs/DELIVERY_ROADMAP.md P1-1a).

1. The office adds "Seljanka" at 6,50 €: it is not sold anywhere yet.
2. The proposer cannot approve the price (four eyes); a second person can.
3. The till shows the product without a reload (live event) at 6,50 €.
4. Outlet B gets its own price list; its price 6,90 € is proposed in the office,
   approved by the second person, and the outlet-B till shows 6,90 € while
   outlet A keeps 6,50 €.
5. A later base price change does not touch an earlier sale; R14 stays green.
"""

from __future__ import annotations

import sqlite3
import uuid

import httpx
import pytest
from playwright.sync_api import expect

from conftest import Server

SECOND = {"X-Tenant-ID": "demo", "X-Cashier-ID": "mari", "X-Role": "manager"}   # the other person


@pytest.fixture
def shop(browser, tmp_path):
    srv = Server(tmp_path)
    con = sqlite3.connect(srv.env["AXAR_POS_DATABASE_URL"].removeprefix("sqlite:///"))
    cur = con.execute("INSERT INTO locations (tenant_id, code, name) VALUES ('demo', 'SADAM', 'Sadama kohvik')")
    con.execute("INSERT INTO registers (tenant_id, location_id, code, name, active, receipt_floor) "
                "VALUES ('demo', ?, 'SADAM1', 'Sadama kassa', 1, 1)", (cur.lastrowid,))
    con.commit()
    con.close()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    p = ctx.new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.on("dialog", lambda d: d.accept())
    p.srv = srv
    yield p
    assert errors == [], errors
    ctx.close()
    srv.stop()


def second(srv, method, path, body=None):
    h = {**SECOND, "Idempotency-Key": str(uuid.uuid4())}
    r = httpx.request(method, f"{srv.base}{path}", json=body, headers=h, timeout=5)
    assert r.status_code in (200, 201), r.text
    return r.json()


def pending(srv):
    return second(srv, "GET", "/api/v1/pricing/prices/pending")


def tile(p, name):
    return p.locator("#product-grid .product", has_text=name)


def test_new_product_four_eyes_live_till_and_outlet_price(shop):
    p, srv = shop, shop.srv
    p.goto(f"{srv.base}/one/")
    p.wait_for_selector("#product-grid .product")
    p.click('[data-mode="office"]')
    p.click("#office-tab-catalog")

    # 1. new product: a proposal, not sold yet
    p.fill("#new-sku", "SELJ")
    p.fill("#new-name", "Seljanka")
    p.fill("#new-price", "6,50")
    p.click("#new-submit")
    expect(p.locator("#cat-pending")).to_contain_text("Seljanka · Põhihind: 6,50 €")
    assert not any(x["sku"] == "SELJ" for x in srv.api("/api/v1/products"))

    # 2. four eyes: the proposer (this browser) cannot approve
    row = p.locator("#cat-pending li[data-version]").first
    row.locator(".cat-approve").click()
    expect(row).to_contain_text("Enda esitatud hinda ei saa kinnitada")
    second(srv, "POST", f"/api/v1/pricing/prices/{pending(srv)[0]['id']}/approve", {})

    # 3. the till shows it without a reload
    p.click('[data-mode="express"]')
    expect(tile(p, "Seljanka")).to_contain_text("6,50 €", timeout=10_000)

    # 4. outlet B: own price list, own price
    p.click('[data-mode="office"]')
    p.click("#office-tab-catalog")
    p.fill("#list-name", "Sadam")
    p.click("#list-submit")
    lists = second(srv, "GET", "/api/v1/pricing/lists")
    sadam = next(x for x in lists if x["name"] == "Sadam")["id"]
    outlet = next(o for o in second(srv, "GET", "/api/v1/pricing/outlets") if o["code"] == "SADAM")["id"]
    p.select_option(f'tr[data-outlet="{outlet}"] select.outlet-list', str(sadam))
    p.fill("#cat-search", "Seljanka")
    p.locator(".cat-open", has_text="Seljanka").click()
    p.fill(f'#cat-prices tr[data-list="{sadam}"] .cat-new-price', "6,90")
    p.fill("#price-reason", "sadama hind")
    p.click("#price-submit")
    expect(p.locator("#cat-pending")).to_contain_text("Seljanka · Sadam: 6,90 €")
    second(srv, "POST", f"/api/v1/pricing/prices/{pending(srv)[0]['id']}/approve", {})

    regs = {r["code"]: r["id"] for r in srv.api("/api/v1/registers")}
    p.click('[data-mode="express"]')
    p.select_option("#register-select", str(regs["SADAM1"]))
    expect(tile(p, "Seljanka")).to_contain_text("6,90 €", timeout=10_000)
    p.select_option("#register-select", str(regs["KASSA1"]))
    expect(tile(p, "Seljanka")).to_contain_text("6,50 €", timeout=10_000)

    # 5. an earlier sale keeps its price after a base price change
    pid = next(x["id"] for x in srv.api("/api/v1/products") if x["sku"] == "SELJ")
    second(srv, "POST", "/api/v1/drawer/open", {"register_id": regs["KASSA1"], "opening_float_cents": 5000})
    sale = second(srv, "POST", "/api/v1/sales/cash", {"register_id": regs["KASSA1"], "tendered_cents": 650,
                                                      "lines": [{"product_id": pid, "quantity": 1}]})
    v = httpx.post(f"{srv.base}/api/v1/pricing/prices", json={"product_id": pid, "price_cents": 700},
                   headers={"X-Tenant-ID": "demo", "X-Cashier-ID": "kassa1", "X-Role": "manager"}, timeout=5).json()
    second(srv, "POST", f"/api/v1/pricing/prices/{v['id']}/approve", {})
    expect(tile(p, "Seljanka")).to_contain_text("7,00 €", timeout=10_000)
    assert second(srv, "GET", f"/api/v1/sales/{sale['id']}")["lines"][0]["unit_price_cents"] == 650
    rep = second(srv, "GET", "/api/v1/admin/reconciliation")
    r14 = next(c for c in rep["checks"] if c["check"] == "R14.sale_prices")
    assert r14["status"] == "ok" and r14["problem_count"] == 0
