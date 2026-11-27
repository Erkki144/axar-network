"""#60 P0-2: office screens in the real browser against a real server.

1. Reconciliation: a real day shows every rule; a register changed behind the
   application turns exactly its row red; the sale trace shows the chain.
2. POS settings: the manager changes the offline limit 72 h -> 24 h; the
   central server keeps it (the till receives it on its next contact:
   tests/test_offline_limit.py::test_manager_sets_the_limit_and_the_till_gets_it_on_the_next_contact).
3. Offline review queue: a real till (edge node, own SQLite) syncs a sale
   while the central drawer is closed -> quarantined; the manager opens the
   drawer and presses "Proovi uuesti" -> applied exactly once.
"""

from __future__ import annotations

import sqlite3
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from axar_pos import reconcile

from axar_pos import offline
from axar_pos.api import create_app
from axar_pos.config import Settings
from axar_pos.db import seed_demo
from axar_pos.sync_agent import SyncAgent
from conftest import HEADERS, Server

MANAGER = {**HEADERS, "X-Role": "manager"}


@pytest.fixture
def office(browser, tmp_path):
    srv = Server(tmp_path, {"AXAR_POS_INPROCESS_WORKER": "1"})
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    p = ctx.new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.srv = srv
    yield p
    assert errors == [], errors
    ctx.close()
    srv.stop()


def post(srv, path, body, key=True):
    h = {**MANAGER, **({"Idempotency-Key": str(uuid.uuid4())} if key else {})}
    r = httpx.post(f"{srv.base}{path}", json=body, headers=h, timeout=5)
    assert r.status_code in (200, 201), r.text
    return r.json()


def registers(srv):
    return {r["code"]: r["id"] for r in srv.api("/api/v1/registers")}


def open_office(p):
    """#76: the office is its own application at /office/ (demo identity here)."""
    p.goto(f"{p.srv.base}/office/")
    p.wait_for_selector("#office-tab-recon")


def test_reconciliation_screen_names_a_tampered_register_and_traces_a_sale(office):
    p, srv = office, office.srv
    reg = registers(srv)["KASSA1"]
    drawer = post(srv, "/api/v1/drawer/open", {"register_id": reg, "opening_float_cents": 5000}, key=False)
    pid = srv.api("/api/v1/products")[0]["id"]
    sale = post(srv, "/api/v1/sales/cash", {"register_id": reg, "lines": [{"product_id": pid, "quantity": 2}],
                                            "tendered_cents": 5000})
    open_office(p)
    p.click("#recon-run")
    rows = p.locator("#recon-list .recon-row")
    expect(rows).to_have_count(len(reconcile.RULES))
    expect(p.locator("#recon-list .recon-row.fail")).to_have_count(0)
    expect(p.locator('[data-check="R1.drawer_cash"]')).to_have_class("recon-row ok")

    con = sqlite3.connect(srv.env["AXAR_POS_DATABASE_URL"].removeprefix("sqlite:///"))
    con.execute("UPDATE drawer_sessions SET cash_sales_cents = cash_sales_cents + 100 WHERE id = ?", (drawer["id"],))
    con.commit()
    con.close()
    p.click("#recon-run")
    expect(p.locator("#recon-list .recon-row.fail")).to_have_count(1)
    bad = p.locator('[data-check="R1.drawer_cash"]')
    expect(bad).to_have_class("recon-row fail")
    expect(bad).to_contain_text("VIGA")
    bad.locator("summary").click()
    expect(bad.locator(".recon-detail")).to_contain_text(f'"drawer_id":{drawer["id"]}')

    p.fill("#trace-sale-id", sale["id"])
    p.click("#trace-run")
    trace = p.locator("#trace-list")
    expect(trace).to_contain_text("kassa KASSA1")
    expect(trace).to_contain_text("pos.sale.completed")
    p.fill("#trace-sale-id", "no-such-sale")
    p.click("#trace-run")
    expect(p.locator("#trace-out")).to_contain_text("Sellist müüki ei leitud")


def test_manager_sets_the_offline_limit_in_the_settings_screen(office):
    p, srv = office, office.srv
    open_office(p)
    p.click("#office-tab-settings")
    expect(p.locator("#offline-limit")).to_have_value("72")
    p.fill("#offline-limit", "73")
    p.click("#settings-save")
    expect(p.locator("#settings-msg")).to_contain_text("1–72")
    p.fill("#offline-limit", "24")
    p.click("#settings-save")
    expect(p.locator("#settings-msg")).to_contain_text("Salvestatud: 24 h")
    got = httpx.get(f"{srv.base}/api/v1/admin/pos-settings", headers=MANAGER, timeout=5).json()
    assert got["offline_limit_hours"] == 24
    open_office(p)
    p.click("#office-tab-settings")
    expect(p.locator("#offline-limit")).to_have_value("24")


@pytest.fixture
def till(tmp_path):
    """A real edge node: own SQLite, same demo catalog, AXAR_POS_EDGE_DEVICE_ID."""
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'till.db'}", demo_identity=True,
                        edge_device_id="e2e-till")
    app = create_app(settings)
    seed_demo(app.state.session_factory)
    with app.state.session_factory() as s:
        offline.record_contact(s, tenant_id="demo", device_id="e2e-till", now=settings.clock(), limit_hours=72)
    with TestClient(app) as c:
        c.app_ = app
        c.settings = settings
        yield c
    app.state.engine.dispose()


def test_quarantined_till_entry_is_retried_once_from_the_review_screen(office, till):
    p, srv = office, office.srv
    reg = next(r["id"] for r in till.get("/api/v1/registers", headers=HEADERS).json() if r["code"] == "KASSA1")
    pid = till.get("/api/v1/products", headers=HEADERS).json()[0]["id"]
    assert till.post("/api/v1/drawer/open", json={"register_id": reg, "opening_float_cents": 0},
                     headers=HEADERS).status_code == 201
    r = till.post("/api/v1/sales/cash", json={"register_id": reg, "lines": [{"product_id": pid, "quantity": 1}],
                                              "tendered_cents": 1000},
                  headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 201, r.text

    def send(body):
        resp = httpx.post(f"{srv.base}/api/v1/sync/push", json=body, headers=HEADERS, timeout=5)
        return resp.status_code, resp.json()

    agent = SyncAgent(till.app_.state.session_factory, "demo", "e2e-till", send, till.settings.clock)
    agent.run_until_idle()                                     # central drawer closed -> quarantined

    open_office(p)
    p.click("#office-tab-quarantine")
    rows = p.locator("#quarantine-list .recon-row")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("Müük")
    expect(rows.first).to_contain_text("drawer")
    rows.first.locator(".q-retry").click()                     # cause not fixed yet: still refused
    expect(rows.first).to_contain_text("still cannot be applied")
    rows.first.locator(".q-dismiss").click()                   # dismiss needs a reason
    expect(rows.first).to_contain_text("vähemalt 5 märki")

    central_reg = registers(srv)["KASSA1"]
    post(srv, "/api/v1/drawer/open", {"register_id": central_reg, "opening_float_cents": 0}, key=False)
    rows.first.locator(".q-retry").click()
    expect(p.locator("#office-body")).to_contain_text("Läbivaatust ootavaid kirjeid pole")
    sales = srv.api("/api/v1/sales")
    assert len(sales) == 1                                      # applied exactly once
    rep = httpx.get(f"{srv.base}/api/v1/admin/reconciliation", headers=MANAGER, timeout=5).json()
    assert rep["ok"], rep
    r13 = next(c for c in rep["checks"] if c["check"] == "R13.offline_sync")
    assert r13["status"] == "ok" and r13["checked"] == 1
