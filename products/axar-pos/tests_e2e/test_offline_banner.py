"""Offline limit in the cashier UI (O1c, owner decision: at most 72 h offline).
A real edge server (AXAR_POS_EDGE_DEVICE_ID) on its own SQLite file; the
till's last contact with the central server is set directly in that file."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from playwright.sync_api import expect
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from axar_pos.models import EdgeSyncState
from conftest import Server
from test_cashier_ui import add, open_drawer, pay, sales

DEVICE = "e2e-till"


def set_contact(srv, hours_ago: float, limit: int = 72):
    eng = create_engine(srv.env["AXAR_POS_DATABASE_URL"])
    now = datetime.now(timezone.utc)
    with Session(eng) as s:
        st = s.get(EdgeSyncState, ("demo", DEVICE))
        if st is None:
            st = EdgeSyncState(tenant_id="demo", device_id=DEVICE)
            s.add(st)
        st.last_contact_at = st.max_seen_at = now - timedelta(hours=hours_ago)
        st.offline_limit_hours = limit
        s.commit()
    eng.dispose()


@pytest.fixture
def edge_page(browser, tmp_path):
    srv = Server(tmp_path, {"AXAR_POS_EDGE_DEVICE_ID": DEVICE})
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    p = ctx.new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.srv = srv
    yield p
    assert errors == [], errors
    ctx.close()
    srv.stop()


def open_till(p):
    p.goto(f"{p.srv.base}/one/")
    p.wait_for_selector("#product-grid .product")


def test_warning_before_the_limit_and_selling_still_works(edge_page):
    p = edge_page
    set_contact(p.srv, hours_ago=63)                                  # 9 h left of 72
    open_till(p)
    banner = p.locator("#offline-banner")
    expect(banner).to_be_visible()
    expect(banner).to_have_class("offline-banner warn")
    expect(banner).to_contain_text("Kassa lukustub 8 h")
    expect(banner).to_contain_text("piirang 72 h")
    open_drawer(p)
    add(p, "Kohv")
    pay(p, "10,00")
    expect(p.locator("#change-due")).to_be_visible()
    assert len(sales(p.srv)) == 1


def test_locked_till_explains_why_keeps_the_basket_and_sells_after_contact(edge_page):
    p = edge_page
    set_contact(p.srv, hours_ago=1)
    open_till(p)
    expect(p.locator("#offline-banner")).to_be_hidden()               # plenty of time: no banner
    open_drawer(p)
    add(p, "Kohv")
    set_contact(p.srv, hours_ago=80)                                  # the window ran out meanwhile
    pay(p, "10,00")
    expect(p.locator("#modal-content")).to_contain_text("Makset ei salvestatud: Kassa on lukus")
    expect(p.locator("#modal-content")).not_to_contain_text("teadmata")          # definitive, not "unknown"
    banner = p.locator("#offline-banner")
    expect(banner).to_have_class("offline-banner blocked")            # refreshed by the 423 answer
    expect(banner).to_contain_text("Senised müügid on alles")
    assert sales(p.srv) == []
    expect(p.locator("#basket-count")).to_have_text("1")             # nothing lost at the till either

    set_contact(p.srv, hours_ago=0)                                   # connection back (sync agent)
    p.reload()
    p.wait_for_selector("#product-grid .product")
    expect(p.locator("#offline-banner")).to_be_hidden()
    if p.locator("#basket-count").inner_text() == "0":
        add(p, "Kohv")
    pay(p, "10,00")
    expect(p.locator("#change-due")).to_be_visible()
    assert len(sales(p.srv)) == 1


def test_clock_set_back_is_explained(edge_page):
    p = edge_page
    set_contact(p.srv, hours_ago=-3)                                  # till saw a time 3 h ahead of now
    open_till(p)
    banner = p.locator("#offline-banner")
    expect(banner).to_have_class("offline-banner blocked")
    expect(banner).to_contain_text("kell on tagasi keeratud")


def test_central_server_shows_no_banner(page_central):
    expect(page_central.locator("#offline-banner")).to_be_hidden()


@pytest.fixture
def page_central(browser, server):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    p = ctx.new_page()
    seen = []
    p.on("request", lambda r: seen.append(r.url) if "/sync/status" in r.url else None)
    p.goto(f"{server.base}/one/")
    p.wait_for_selector("#product-grid .product")
    p.wait_for_timeout(300)
    assert len(seen) == 1                                             # asked once, 404, then stops
    yield p
    ctx.close()
