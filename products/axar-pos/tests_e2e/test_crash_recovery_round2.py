"""AION review round 2 on PR #12: cleanup-before-delete ordering, cross-tab
atomicity (Web Locks), non-definitive 4xx, strict journal schema."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

from test_cashier_ui import add, open_drawer, sales, wait_quote

PENDING = "axar.one.pending.v1"
DEMO = "axar.one.demo.v1"


def journal(page: Page):
    return page.evaluate(f"localStorage.getItem('{PENDING}')")


def table_bill(page: Page, table: str, product: str, n: int = 1):
    page.click('[data-mode="tables"]')
    page.locator("#table-grid .table-card", has_text=table).click()
    add(page, product, n)
    wait_quote(page)


def start_pay(page: Page, tender: str):
    page.click("#pay-button")
    page.fill("#tender-input", tender)


def break_storage_key(page: Page, method: str, key: str):
    page.evaluate("""([m, k]) => { const o = Storage.prototype[m];
        Storage.prototype[m] = function (kk, ...rest) {
          if (kk === k) throw new Error('quota'); return o.call(this, kk, ...rest); }; }""", [method, key])


# ------------------------------------------- 1. cleanup before journal delete


def test_cleanup_failure_keeps_journal_then_recovery_finishes_once(phone, server):
    open_drawer(phone)
    table_bill(phone, "Laud 02", "Päevasupp")
    break_storage_key(phone, "setItem", DEMO)          # bill cleanup will fail
    start_pay(phone, "10,00")
    phone.click("#confirm-payment")
    expect(phone.locator("#change-due")).to_be_visible()   # server confirmed
    expect(phone.locator(".pay-state.unconfirmed")).to_contain_text("lauaarvet ei õnnestunud")
    assert journal(phone) is not None                  # NOT deleted: cleanup failed
    assert len(sales(server)) == 1

    phone.reload()                                     # storage works again
    phone.wait_for_selector("#recovery")
    phone.click("#recover-retry")                      # replay same key
    expect(phone.locator("#change-due")).to_be_visible()
    assert journal(phone) is None
    assert len(sales(server)) == 1                     # still exactly one
    phone.click("#next-customer")
    phone.click('[data-mode="tables"]')
    phone.locator("#table-grid .table-card", has_text="Laud 02").click()
    expect(phone.locator("#basket-count")).to_have_text("0")   # bill cleared


def test_crash_after_cleanup_before_journal_delete_is_safe(phone, server):
    open_drawer(phone)
    table_bill(phone, "Laud 03", "Päevasupp")
    break_storage_key(phone, "removeItem", PENDING)    # "crash" before journal delete
    start_pay(phone, "10,00")
    phone.click("#confirm-payment")
    expect(phone.locator("#change-due")).to_be_visible()
    assert journal(phone) is not None
    phone.reload()
    phone.wait_for_selector("#recovery")
    phone.click("#recover-retry")
    expect(phone.locator("#change-due")).to_be_visible()
    assert journal(phone) is None
    assert len(sales(server)) == 1


# --------------------------------------------------- 2. cross-tab atomicity


def test_no_web_locks_blocks_payment(browser, server):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.add_init_script("Object.defineProperty(Navigator.prototype, 'locks', { get: () => undefined });")
    page = ctx.new_page()
    page.goto(f"{server.base}/one/")
    page.wait_for_selector("#product-grid .product")
    open_drawer(page)
    add(page, "Kohv")
    expect(page.locator("#total")).to_have_text("2,50 €")
    expect(page.locator("#quote-state")).to_contain_text("akende lukustust")
    expect(page.locator("#pay-button")).to_be_disabled()
    assert sales(server) == []
    ctx.close()


def test_lock_held_by_other_tab_refuses_without_sending(phone, server):
    open_drawer(phone)
    add(phone, "Kohv")
    wait_quote(phone)
    other = phone.context.new_page()
    other.goto(f"{server.base}/one/")
    other.wait_for_selector("#product-grid .product")
    other.evaluate("() => { navigator.locks.request('axar-one-money', () => new Promise(() => {})); }")
    posts = []
    phone.on("request", lambda r: posts.append(r) if r.method == "POST" and r.url.endswith("/api/v1/sales/cash") else None)
    start_pay(phone, "2,50")
    phone.click("#confirm-payment")
    expect(phone.locator(".pay-state.error")).to_contain_text("teises aknas")
    assert posts == [] and sales(server) == [] and journal(phone) is None
    other.close()


def test_simultaneous_payment_of_same_table_from_two_tabs(phone, server):
    open_drawer(phone)
    table_bill(phone, "Laud 04", "Päevasupp", 2)
    tab2 = phone.context.new_page()
    tab2.goto(f"{server.base}/one/")
    tab2.wait_for_selector("#product-grid .product")
    table_bill(tab2, "Laud 04", "Päevasupp", 0)            # same bill, loaded from storage
    expect(tab2.locator("#basket-count")).to_have_text("2")
    for p in (phone, tab2):
        start_pay(p, "20,00")
    # Barrier: both tabs click at the same wall-clock instant.
    at = phone.evaluate("Date.now() + 500")
    for p in (phone, tab2):
        p.evaluate("(t) => setTimeout(() => document.getElementById('confirm-payment').click(), t - Date.now())", at)
    phone.wait_for_timeout(2500)
    assert len(sales(server)) == 1                          # exactly one sale
    reg = server.api("/api/v1/registers")[0]
    assert server.api(f"/api/v1/registers/{reg['id']}/drawer")["drawer"]["sale_count"] == 1
    done = [p for p in (phone, tab2) if p.locator("#change-due").count()]
    assert len(done) == 1
    tab2.close()


# ------------------------------------------------ 3. non-definitive 4xx keep


@pytest.mark.parametrize("status,payload", [
    (401, {"detail": "X-Tenant-ID and X-Cashier-ID headers required"}),
    (403, {"detail": "unknown tenant"}),
    (429, {"detail": "rate limited"}),
    (409, {"error": "idempotency_key_reused", "message": "reused"}),
])
def test_uncertain_4xx_after_lost_response_keeps_journal(phone, server, status, payload):
    open_drawer(phone)
    calls = []

    def handler(route):
        calls.append(route.request.headers.get("idempotency-key"))
        if len(calls) == 1:
            route.fetch(); route.abort("connectionreset")         # committed, response lost
        elif len(calls) == 2:
            route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))
        else:
            route.continue_()

    phone.route("**/api/v1/sales/cash", handler)
    add(phone, "Latte")
    wait_quote(phone)
    start_pay(phone, "5,00")
    phone.click("#confirm-payment")
    expect(phone.locator(".pay-state.unconfirmed")).to_be_visible()
    phone.click("#confirm-payment")                             # gets the 4xx
    expect(phone.locator(".pay-state.unconfirmed")).to_be_visible()   # still unknown
    assert journal(phone) is not None
    phone.click("#confirm-payment")                             # real replay
    expect(phone.locator("#change-due")).to_have_text("Tagasi 1,20 €")
    assert len(set(calls)) == 1 and len(sales(server)) == 1


# --------------------------------------------------- 4. strict journal schema


def test_op_path_mismatch_record_is_treated_as_corrupt(phone, server):
    rec = {"v": 1, "op": "sale", "key": "k" * 36,
           "path": "/api/v1/sales/00000000-0000-0000-0000-000000000000/refunds",
           "body": {"register_id": 1, "lines": [{"product_id": 1, "quantity": 1}], "tendered_cents": 250},
           "headers": {}, "cartKey": "walkin:1", "registerId": 1, "createdAt": "2026-11-24T10:00:00Z"}
    phone.evaluate("([k, v]) => localStorage.setItem(k, v)", [PENDING, json.dumps(rec)])
    phone.reload()
    phone.wait_for_selector("#recovery")
    expect(phone.locator("#recovery")).to_contain_text("loetamatu")
    expect(phone.locator("#recover-retry")).to_have_count(0)


def test_unreadable_journal_storage_fails_closed(browser, server):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.add_init_script(f"""(() => {{ const g = Storage.prototype.getItem;
        Storage.prototype.getItem = function (k) {{ if (k === '{PENDING}') throw new Error('io'); return g.call(this, k); }}; }})();""")
    page = ctx.new_page()
    page.goto(f"{server.base}/one/")
    page.wait_for_selector("#recovery")
    expect(page.locator("#recovery")).to_contain_text("loetamatu")
    ctx.close()
