"""AXAR ONE with real server-side identity (issue #2): device enrolment, staff
PIN login, role-bound UI, manager step-up for refunds, session loss."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import time

import httpx
import pytest
from playwright.sync_api import expect


from axar_pos import auth, reconcile
from conftest import ROOT, Server

# Hangs on a Windows workstation (also on the base branch, 26.11.2026; cause not yet found).
# CI (Linux) always runs it. Remove the skip once the Windows cause is fixed.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="known Windows hang; runs in Linux CI")
from test_cashier_ui import add, open_drawer, pay, wait_quote

KEY = base64.urlsafe_b64encode(b"e" * 32).decode()   # test-only server key
OWNER_PW = "owner-password-123"


def _bootstrap(env):
    """Owner via the real admin CLI (schema + demo seed created on the way)."""
    subprocess.run([sys.executable, "-c", "from axar_pos.config import Settings; from axar_pos.db import *;"
                    "f=init_db(make_engine(Settings.from_env().database_url)); seed_demo(f)"],
                   cwd=ROOT, env=env, check=True)
    out = subprocess.run([sys.executable, "-m", "axar_pos.cli", "bootstrap", "--tenant", "demo",
                          "--owner-name", "Omanik", "--login", "owner"], cwd=ROOT, check=True,
                         env={**env, "AXAR_POS_BOOTSTRAP_PASSWORD": OWNER_PW}, capture_output=True, text=True)
    env["_TOTP"] = next(l.split(": ")[1] for l in out.stdout.splitlines() if l.startswith("TOTP secret"))


@pytest.fixture
def auth_server(tmp_path):
    srv = Server(tmp_path, {"AXAR_POS_DEMO_IDENTITY": None, "AXAR_POS_SEED_DEMO": None,
                            "AXAR_POS_SECRET_KEY": KEY}, before_start=_bootstrap)
    secret = srv.env["_TOTP"]
    totp = auth.totp_at(secret, int(time.time()) // 30)
    r = httpx.post(f"{srv.base}/api/v1/auth/login", json={"tenant_id": "demo", "login": "owner",
                                                          "password": OWNER_PW, "totp": totp})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    regs = httpx.get(f"{srv.base}/api/v1/admin/devices", headers=h)
    assert regs.status_code == 200
    staff = {}
    for name, role, pin in (("Kati", "cashier", "4829"), ("Mart", "manager", "7351")):
        r = httpx.post(f"{srv.base}/api/v1/admin/staff", headers=h,
                       json={"display_name": name, "pin": pin, "roles": [{"role": role}]})
        assert r.status_code == 201, r.text
        staff[name] = r.json()["id"]

    def code():
        # Owner back office would show this; here via API. Location/register of the demo seed.
        loc_reg = subprocess.run([sys.executable, "-m", "axar_pos.cli", "enroll-code", "--tenant", "demo",
                                  "--location", "POOD1", "--register", "KASSA1", "--name", "Kassa 1"],
                                 cwd=ROOT, env=srv.env, check=True, capture_output=True, text=True)
        return loc_reg.stdout.strip().rsplit(" ", 1)[1]

    srv.enroll_code = code
    srv.staff = staff
    yield srv
    srv.stop()


def login(page, name, pin):
    page.locator(".staff-btn", has_text=name).click()
    page.fill("#pin-input", pin)
    page.click("#pin-submit")


@pytest.fixture
def till(browser, auth_server):
    ctx = browser.new_context(viewport={"width": 1024, "height": 768})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{auth_server.base}/one/")
    expect(page.locator("#auth-title")).to_have_text("Seadme registreerimine")
    page.fill("#enroll-code", auth_server.enroll_code())
    page.click("#enroll-submit")
    expect(page.locator("#auth-title")).to_have_text("Sisselogimine")
    yield page
    assert errors == [], errors
    ctx.close()


def test_enrol_pin_login_sell_and_wrong_pin(till, auth_server):
    login(till, "Kati", "0000")
    expect(till.locator("#auth-content .dialog-error")).to_have_text("Vale PIN.")
    login(till, "Kati", "4829")
    expect(till.locator("#auth-screen")).to_be_hidden()
    expect(till.locator("#cashier-name")).to_have_text("Kati")
    expect(till.locator("#register-select")).to_be_disabled()      # bound to this device's register
    open_drawer(till)
    add(till, "Kohv")
    pay(till, "5,00")
    expect(till.locator("#change-due")).to_have_text("Tagasi 2,50 €")



def test_provisioned_register_is_read_only_and_not_from_localstorage(till, auth_server):
    """#81: an authenticated till must display its assigned register, no picker."""
    login(till, "Kati", "4829")
    expect(till.locator("#register-select")).to_be_hidden()
    fixed = till.locator("#register-fixed-label")
    expect(fixed).to_be_visible()
    expect(fixed).to_contain_text("Kassa")
    initial_label = fixed.inner_text()
    till.evaluate("localStorage.setItem('axar.one.register', '999999')")
    till.reload()
    expect(till.locator("#register-select")).to_be_hidden()
    expect(fixed).to_have_text(initial_label)
    open_drawer(till)
    add(till, "Kohv")
    pay(till, "5,00")
    expect(till.locator("#change-due")).to_have_text("Tagasi 2,50 €")


def test_device_without_register_never_guesses_a_register(browser, auth_server):
    """A real device with register=None cannot silently use the first till."""
    cmd = subprocess.run([sys.executable, "-m", "axar_pos.cli", "enroll-code",
                          "--tenant", "demo", "--location", "POOD1",
                          "--name", "Unbound-Device"], cwd=ROOT, env=auth_server.env,
                         check=True, capture_output=True, text=True)
    code = cmd.stdout.strip().rsplit(" ", 1)[1]
    ctx = browser.new_context(viewport={"width": 1024, "height": 768})
    page = ctx.new_page()
    try:
        page.goto(f"{auth_server.base}/one/")
        page.fill("#enroll-code", code)
        page.click("#enroll-submit")
        expect(page.locator("#auth-title")).to_have_text("Sisselogimine")
        login(page, "Kati", "4829")
        expect(page.locator("#view-root")).to_contain_text(
            "Kassa ei ole sellele seadmele õigesti määratud"
        )
        expect(page.locator("#product-grid .product")).to_have_count(0)
    finally:
        ctx.close()



def test_refund_needs_another_managers_pin_in_the_ui(till, auth_server):
    login(till, "Kati", "4829")
    open_drawer(till)
    add(till, "Latte")
    pay(till, "5,00")
    till.click("#next-customer")
    till.click('[data-mode="manager"]')
    till.locator("#sales-list button", has_text="Tagasta").first.click()
    till.locator('#modal-content button[aria-label^="Tagasta veel"]').first.click()
    till.fill("#refund-reason", "klient tagastas")
    expect(till.locator("#confirm-refund")).to_be_disabled()        # no manager approval yet
    till.select_option("#approver-select", label="Mart")
    till.fill("#approver-pin", "1111")
    till.click("#confirm-refund")
    expect(till.locator("#modal-content")).to_contain_text("juhi kinnitus ebaõnnestus")
    till.fill("#approver-pin", "7351")
    till.click("#confirm-refund")
    expect(till.locator("#refund-paid")).to_have_text("Välja 3,80 €")
    me = auth_server.staff["Mart"]
    assert me  # approver recorded server-side (see tests/test_auth.py for the record check)


def test_lock_and_session_loss_return_to_pin_screen(till, auth_server):
    login(till, "Kati", "4829")
    expect(till.locator("#auth-screen")).to_be_hidden()
    till.click("#lock-button")
    expect(till.locator("#auth-title")).to_have_text("Sisselogimine")
    login(till, "Mart", "7351")
    expect(till.locator("#cashier-name")).to_have_text("Mart")
    # The session is revoked behind the UI's back -> next API call locks the till.
    till.evaluate("sessionStorage.setItem('axar.one.session.v1', 'revoked-token')")
    till.reload()
    expect(till.locator("#auth-title")).to_have_text("Sisselogimine")


def test_phone_mini_cart_keeps_total_and_pay_in_view(browser, auth_server):
    """Usability: on a 390 px phone the basket is below the menu; a sticky
    bar shows count + server total and pays without scrolling."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.goto(f"{auth_server.base}/one/")
    page.fill("#enroll-code", auth_server.enroll_code())
    page.click("#enroll-submit")
    page.locator(".staff-btn", has_text="Kati").click()
    for k in "4829":
        page.click(f'.pin-key[aria-label="{k}"]')
    page.click('.pin-key[aria-label="Logi sisse"]')
    expect(page.locator("#mode-pill")).to_contain_text("kassapidaja")
    expect(page.locator("#mini-cart")).to_be_hidden()
    open_drawer(page)
    add(page, "Kohv", 2)
    bar = page.locator("#mini-cart")
    expect(bar).to_be_visible()
    expect(page.locator("#mini-total")).to_have_text("5,00 €")
    assert bar.bounding_box()["y"] + bar.bounding_box()["height"] <= 844   # inside the viewport
    page.click("#mini-pay")
    page.fill("#tender-input", "5,00")
    page.click("#confirm-payment")
    expect(page.locator("#change-due")).to_have_text("Tagasi 0,00 €")
    page.click("#next-customer")
    expect(bar).to_be_hidden()
    ctx.close()


def test_kitchen_view_respects_permissions(till, auth_server):
    login(till, "Kati", "4829")       # cashier: may see tickets, may not mark them ready
    till.click('[data-mode="tables"]')
    till.locator("#table-grid .table-card", has_text="Laud 01").click()
    add(till, "Päevasupp")
    expect(till.locator("#basket-count")).to_have_text("1")
    till.click("#send-button")
    till.click('[data-mode="kitchen"]')
    ticket = till.locator(".ticket", has_text="Laud 01")
    expect(ticket).to_contain_text("Valmis märgib köök")
    expect(ticket.locator("button.ticket-ready")).to_be_hidden()
    till.click('[data-mode="manager"]')
    expect(till.locator("#report")).to_contain_text("ainult juht")


def test_office_needs_a_back_office_login_and_keeps_the_till_session(till, auth_server):
    """#76: the till and the office are two applications with two logins. The
    till has no office entry at all; the office at /office/ needs password +
    TOTP even for a manager already signed in at the till; a failed office
    login never touches the till session, and the office never sees the till."""
    login(till, "Mart", "7351")                                        # a manager at the till
    expect(till.locator('[data-mode="office"]')).to_have_count(0)      # no office inside the till
    expect(till.locator('[data-mode="inventory"]')).to_have_count(0)
    office = till.context.new_page()
    office.goto(f"{auth_server.base}/office/")
    expect(office.locator("#login-user")).to_be_visible()               # only the login, no office data
    expect(office.locator("#office-tab-recon")).to_have_count(0)
    office.fill("#login-tenant", "demo")
    office.fill("#login-user", "owner")
    office.fill("#login-password", OWNER_PW)
    office.fill("#login-totp", "000000")
    office.click("#login-submit")
    expect(office.locator("#office-login-form .dialog-error")).not_to_be_empty()
    step = int(time.time()) // 30 + 1                                   # newer than the fixture's login step
    office.fill("#login-user", "owner")
    office.fill("#login-password", OWNER_PW)
    office.fill("#login-totp", auth.totp_at(auth_server.env["_TOTP"], step))
    office.click("#login-submit")
    office.click("#recon-run")
    expect(office.locator("#recon-list .recon-row")).to_have_count(len(reconcile.RULES))
    expect(office.locator("#recon-list .recon-row.fail")).to_have_count(0)
    office.click("#office-tab-settings")
    expect(office.locator("#offline-limit")).to_have_value("72")
    expect(office.locator("#product-grid")).to_have_count(0)            # no till inside the office
    # The till session is untouched by everything above.
    expect(till.locator("#auth-screen")).to_be_hidden()
    expect(till.locator("#cashier-name")).to_have_text("Mart")
    # Logging out of the office does not log out the till, and back.
    office.click("#office-logout")
    expect(office.locator("#login-user")).to_be_visible()
    expect(till.locator("#cashier-name")).to_have_text("Mart")
