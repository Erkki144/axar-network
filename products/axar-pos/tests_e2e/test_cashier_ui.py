"""AXAR ONE cashier UI against the real AXAR POS API (issue #11 acceptance tests)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from playwright.sync_api import Page, expect

REPORT = Path(os.environ.get("AXAR_E2E_REPORT", "")) if os.environ.get("AXAR_E2E_REPORT") else None


# ----------------------------------------------------------------- helpers


def open_drawer(page: Page, float_eur: str = "50,00"):
    page.click("#open-drawer")
    page.fill("#float-input", float_eur)
    page.click("#confirm-open-drawer")
    expect(page.locator("#drawer-status")).to_contain_text("avatud")


def add(page: Page, name: str, times: int = 1):
    btn = page.locator(f'#product-grid button[aria-label^="Lisa ostukorvi: {name},"]')
    for _ in range(times):
        btn.click()


def wait_quote(page: Page):
    expect(page.locator("#quote-state")).to_have_text("Summad arvutas server.")


def pay(page: Page, tender: str):
    wait_quote(page)
    page.click("#pay-button")
    page.fill("#tender-input", tender)
    page.click("#confirm-payment")


def sales(server):
    return server.api("/api/v1/sales")


def no_horizontal_scroll(page: Page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def record(name, **data):
    if REPORT:
        rows = json.loads(REPORT.read_text()) if REPORT.exists() else {}
        rows[name] = data
        REPORT.write_text(json.dumps(rows, indent=1, ensure_ascii=False))


# --------------------------------------------------------- core sale flows


def test_catalog_comes_from_api_and_sale_uses_server_amounts(phone, server):
    api_names = sorted(p["name"] for p in server.api("/api/v1/products"))
    ui_names = sorted(phone.locator("#product-grid .product .product-name").all_inner_texts())
    assert ui_names == api_names

    clicks = 0
    open_drawer(phone); clicks += 2  # open + confirm (float prefilled)
    add(phone, "Kohv", 2); add(phone, "Kaneelisaiake"); clicks += 3
    wait_quote(phone)
    # Line amounts are the server's (quote), not "…" placeholders.
    expect(phone.locator('.cart-row[data-product] .cart-price')).to_have_text(["5,00 €", "2,95 €"])
    expect(phone.locator("#total")).to_have_text("7,95 €")
    expect(phone.locator("#vat")).to_have_text("1,54 €")
    t0 = time.perf_counter()
    phone.click("#pay-button"); phone.click("#tender-input")
    phone.locator(".quick-amounts button", has_text="10 €").click()
    phone.click("#confirm-payment"); clicks += 3  # pay, 10 €, confirm
    expect(phone.locator("#change-due")).to_have_text("Tagasi 2,05 €")
    latency_ms = round((time.perf_counter() - t0) * 1000)
    expect(phone.locator("#receipt")).to_contain_text("Tšekk nr 1")
    expect(phone.locator("#receipt")).to_contain_text("SIMULEERITUD")

    s = sales(server)
    assert len(s) == 1 and s[0]["total_cents"] == 795 and s[0]["change_cents"] == 205
    phone.click("#next-customer")
    expect(phone.locator("#drawer-status")).to_contain_text("oodatav sularaha 57,95 €")
    expect(phone.locator("#basket-count")).to_have_text("0")
    no_horizontal_scroll(phone)
    record("express_sale_3_items", clicks_after_drawer_open=clicks - 2, ms_pay_to_receipt=latency_ms)


def test_tablet_layout(tablet):
    expect(tablet.locator("#checkout-panel")).to_be_visible()
    expect(tablet.locator("#product-grid")).to_be_visible()
    no_horizontal_scroll(tablet)
    # Touch targets: product buttons and primary action at least 44 px tall.
    assert tablet.locator("#product-grid .product").first.bounding_box()["height"] >= 44
    assert tablet.locator("#pay-button").bounding_box()["height"] >= 44


def test_empty_cart_and_closed_drawer_block_payment(phone):
    expect(phone.locator("#pay-button")).to_be_disabled()
    expect(phone.locator("#quote-state")).to_contain_text("Kassa on suletud")
    add(phone, "Kohv")
    wait_quote_or_closed = phone.locator("#quote-state")
    expect(wait_quote_or_closed).to_contain_text("Kassa on suletud")
    expect(phone.locator("#pay-button")).to_be_disabled()


def test_insufficient_tender_cannot_be_confirmed(phone, server):
    open_drawer(phone)
    add(phone, "Päevasupp")
    wait_quote(phone)
    phone.click("#pay-button")
    phone.fill("#tender-input", "1,00")
    expect(phone.locator("#payment-error")).to_have_text("Sularahast ei piisa.")
    expect(phone.locator("#confirm-payment")).to_be_disabled()
    assert sales(server) == []


def test_cash_rounding_shown_from_server(phone, server):
    open_drawer(phone)
    add(phone, "Võileib")                     # 4,99 -> cash 5,00 (+0,01)
    wait_quote(phone)
    expect(phone.locator("#rounding")).to_have_text("+0,01 €")
    expect(phone.locator("#total")).to_have_text("5,00 €")
    pay(phone, "5,00")
    expect(phone.locator("#receipt")).to_contain_text("Ümardus")
    phone.click("#next-customer")
    add(phone, "Õunamahl")                    # 2,71 -> cash 2,70 (−0,01)
    wait_quote(phone)
    expect(phone.locator("#rounding")).to_have_text("−0,01 €")
    pay(phone, "2,70")
    expect(phone.locator("#change-due")).to_have_text("Tagasi 0,00 €")
    rep = server.api("/api/v1/reports/daily")
    assert rep["cash_rounding_cents"] == 0 and rep["cash_collected_cents"] == 770


# ------------------------------------------------- double tap and failures


def test_double_tap_creates_exactly_one_sale(phone, server):
    open_drawer(phone)
    add(phone, "Kohv")
    wait_quote(phone)
    phone.click("#pay-button")
    phone.fill("#tender-input", "2,50")
    posts = []
    phone.on("request", lambda r: posts.append(r) if r.method == "POST" and r.url.endswith("/api/v1/sales/cash") else None)
    # Three synchronous clicks on the same button = an impatient double/triple tap.
    phone.eval_on_selector("#confirm-payment", "b => { b.click(); b.click(); b.click(); }")
    expect(phone.locator("#change-due")).to_be_visible()
    # UI guard: one request only. (Server idempotency would also dedupe a
    # repeat with the same key; this asserts the client layer on its own.)
    assert len(posts) == 1
    assert len(sales(server)) == 1
    assert server.api(f"/api/v1/registers/{server.api('/api/v1/registers')[0]['id']}/drawer")["drawer"]["sale_count"] == 1


def _intercept_sales(page: Page, first: str):
    """first='after_commit': server records the sale but the client loses the
    response. first='before_commit': the request never reaches the server."""
    keys: list[str] = []

    def handler(route):
        keys.append(route.request.headers.get("idempotency-key", ""))
        if len(keys) == 1:
            if first == "after_commit":
                route.fetch()  # let the server commit, then drop the response
            route.abort("connectionreset")
        else:
            route.continue_()

    page.route("**/api/v1/sales/cash", handler)
    return keys


def test_lost_response_after_commit_retry_returns_same_sale(phone, server):
    open_drawer(phone)
    keys = _intercept_sales(phone, "after_commit")
    add(phone, "Latte")
    pay(phone, "5,00")
    expect(phone.locator(".pay-state.unconfirmed")).to_contain_text("teadmata")
    expect(phone.locator("#tender-input")).to_be_disabled()
    expect(phone.locator("#change-due")).to_have_count(0)  # no success shown
    assert len(sales(server)) == 1                         # server did commit

    phone.click("#confirm-payment")                        # "Proovi uuesti (sama makse)"
    expect(phone.locator("#change-due")).to_have_text("Tagasi 1,20 €")
    assert len(keys) == 2 and keys[0] == keys[1] and len(keys[0]) >= 8
    assert len(sales(server)) == 1                         # replay, not a duplicate


def test_network_failure_before_commit_then_retry(phone, server):
    open_drawer(phone)
    keys = _intercept_sales(phone, "before_commit")
    add(phone, "Tee")
    pay(phone, "2,00")
    expect(phone.locator(".pay-state.unconfirmed")).to_be_visible()
    assert sales(server) == []
    phone.click("#confirm-payment")
    expect(phone.locator("#change-due")).to_have_text("Tagasi 0,00 €")
    assert keys[0] == keys[1]
    assert len(sales(server)) == 1


def test_cart_is_locked_while_payment_unconfirmed(phone, server):
    open_drawer(phone)
    _intercept_sales(phone, "before_commit")
    add(phone, "Tee")
    pay(phone, "2,00")
    expect(phone.locator(".pay-state.unconfirmed")).to_be_visible()
    phone.click("#close-modal")
    add(phone, "Kohv")                                      # must be refused
    expect(phone.locator("#basket-count")).to_have_text("1")
    expect(phone.locator("#pay-button")).to_contain_text("Lõpeta pooleli makse")
    phone.click("#pay-button")
    phone.click("#confirm-payment")
    expect(phone.locator("#change-due")).to_be_visible()
    assert len(sales(server)) == 1


def test_offline_is_visible_and_nothing_is_faked(phone, server):
    open_drawer(phone)
    phone.context.set_offline(True)
    add(phone, "Kohv")
    expect(phone.locator("#quote-state")).to_contain_text("Hinda ei saanud serverist")
    expect(phone.locator("#net-status")).to_contain_text("Ühendus puudub")
    expect(phone.locator("#pay-button")).to_be_disabled()
    phone.context.set_offline(False)
    add(phone, "Kohv")
    wait_quote(phone)
    expect(phone.locator("#net-status")).to_contain_text("Server ühendatud")
    assert sales(server) == []


# ------------------------------------------------ registers, drawer, refund


def test_two_registers_are_isolated(phone, server):
    regs = server.api("/api/v1/registers")
    open_drawer(phone)
    add(phone, "Kohv")
    pay(phone, "2,50")
    phone.click("#next-customer")
    phone.select_option("#register-select", str(regs[1]["id"]))
    expect(phone.locator("#drawer-status")).to_contain_text("Kassa on suletud")
    open_drawer(phone, "20,00")
    add(phone, "Latte")
    pay(phone, "3,80")
    phone.click("#next-customer")
    d1 = server.api(f"/api/v1/registers/{regs[0]['id']}/drawer")["drawer"]
    d2 = server.api(f"/api/v1/registers/{regs[1]['id']}/drawer")["drawer"]
    assert (d1["expected_cents"], d1["sale_count"]) == (5250, 1)
    assert (d2["expected_cents"], d2["sale_count"]) == (2380, 1)
    by_reg = {x["register_id"]: x["total_cents"] for x in server.api("/api/v1/reports/daily")["by_register"]}
    assert by_reg == {regs[0]["id"]: 250, regs[1]["id"]: 380}


def test_partial_refund_from_manager_view(phone, server):
    open_drawer(phone)
    add(phone, "Kohv", 2)
    pay(phone, "5,00")
    phone.click("#next-customer")
    phone.click('[data-mode="manager"]')
    phone.locator("#sales-list button", has_text="Tagasta").first.click()
    phone.locator('button[aria-label="Tagasta veel: Kohv"]').click()
    phone.fill("#refund-reason", "klient muutis meelt")
    phone.click("#confirm-refund")
    expect(phone.locator("#refund-paid")).to_have_text("Välja 2,50 €")
    expect(phone.locator("#refund-receipt")).to_contain_text("TAGASTUS")
    sale = server.api(f"/api/v1/sales/{sales(server)[0]['id']}")
    assert sale["lines"][0]["refunded_quantity"] == 1
    drawer = server.api(f"/api/v1/registers/{server.api('/api/v1/registers')[0]['id']}/drawer")["drawer"]
    assert drawer["expected_cents"] == 5000 + 500 - 250


def test_close_drawer_requires_confirmation_and_shows_variance(phone, server):
    open_drawer(phone)
    add(phone, "Kohv")
    pay(phone, "2,50")
    phone.click("#next-customer")
    phone.click('[data-mode="manager"]')
    phone.click("#close-drawer")
    phone.fill("#counted-input", "52,00")
    phone.click("#close-next")
    expect(phone.locator("#modal-content")).to_contain_text("vahe −0,50 €")
    reg = server.api("/api/v1/registers")[0]
    assert server.api(f"/api/v1/registers/{reg['id']}/drawer")["drawer"] is not None  # not closed yet
    phone.click("#confirm-close")
    expect(phone.locator("#modal-content")).to_contain_text("Kassa suletud")
    assert server.api(f"/api/v1/registers/{reg['id']}/drawer")["drawer"] is None


# ---------------------------------------------------------- tables (DEMO)


def test_table_bill_survives_reload_and_is_paid_via_api(phone, server):
    open_drawer(phone)
    phone.click('[data-mode="tables"]')
    expect(phone.locator(".demo-banner")).to_contain_text("DEMO")
    phone.locator("#table-grid .table-card", has_text="Laud 02").click()
    add(phone, "Päevasupp", 2)
    phone.click("#send-button")
    phone.reload()
    phone.wait_for_selector("#product-grid .product")
    phone.click('[data-mode="tables"]')
    phone.locator("#table-grid .table-card", has_text="Laud 02").click()
    expect(phone.locator("#basket-count")).to_have_text("2")      # bill not lost
    expect(phone.locator("#send-button")).to_contain_text("kööki saadetud")
    phone.click('[data-mode="kitchen"]')
    expect(phone.locator(".ticket")).to_contain_text("2 × Päevasupp")
    phone.click('[data-mode="tables"]')
    phone.locator("#table-grid .table-card", has_text="Laud 02").click()
    pay(phone, "20,00")
    expect(phone.locator("#change-due")).to_have_text("Tagasi 8,20 €")
    phone.click("#next-customer")
    expect(phone.locator("#basket-count")).to_have_text("0")
    assert len(sales(server)) == 1 and sales(server)[0]["total_cents"] == 1180
