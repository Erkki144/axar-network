"""AION P0 on PR #12: a pending payment/refund must survive reload, crash and
second tabs. Retrying must reuse the same Idempotency-Key and body, so a sale
or cash payout can never be recorded twice."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from test_cashier_ui import add, open_drawer, pay, sales, wait_quote

PENDING = "axar.one.pending.v1"


def drawer(server):
    reg = server.api("/api/v1/registers")[0]
    return server.api(f"/api/v1/registers/{reg['id']}/drawer")["drawer"]


def drop_first_response(page: Page, pattern: str):
    """Server commits the first request; the browser never sees the response."""
    keys: list[str] = []

    def handler(route):
        keys.append(route.request.headers.get("idempotency-key", ""))
        if len(keys) == 1:
            route.fetch()
            route.abort("connectionreset")
        else:
            route.continue_()

    page.route(pattern, handler)
    return keys


def test_sale_committed_then_reload_recovers_with_same_key(phone, server):
    open_drawer(phone)
    keys = drop_first_response(phone, "**/api/v1/sales/cash")
    add(phone, "Latte")
    pay(phone, "5,00")
    expect(phone.locator(".pay-state.unconfirmed")).to_be_visible()
    assert len(sales(server)) == 1                      # committed on the server
    assert phone.evaluate(f"localStorage.getItem('{PENDING}')") is not None

    phone.reload()                                      # crash / refresh
    phone.wait_for_selector("#recovery")
    expect(phone.locator("#recovery")).to_contain_text("Pooleli makse")
    # Blocking: cannot be dismissed, cashier cannot start a new sale.
    expect(phone.locator("#close-modal")).to_be_hidden()
    phone.keyboard.press("Escape")
    expect(phone.locator("#modal")).to_be_visible()

    phone.click("#recover-retry")
    expect(phone.locator("#change-due")).to_have_text("Tagasi 1,20 €")
    assert len(keys) == 2 and keys[0] == keys[1]
    assert len(sales(server)) == 1                      # no duplicate
    assert drawer(server)["sale_count"] == 1
    assert phone.evaluate(f"localStorage.getItem('{PENDING}')") is None


def test_refund_committed_then_reload_recovers_with_same_key(phone, server):
    open_drawer(phone)
    add(phone, "Kohv", 2)
    pay(phone, "5,00")
    phone.click("#next-customer")
    phone.click('[data-mode="manager"]')
    keys = drop_first_response(phone, "**/api/v1/sales/*/refunds")
    phone.locator("#sales-list button", has_text="Tagasta").first.click()
    phone.locator('button[aria-label="Tagasta veel: Kohv"]').click()
    phone.fill("#refund-reason", "klient muutis meelt")
    phone.click("#confirm-refund")
    expect(phone.locator(".pay-state.unconfirmed")).to_be_visible()

    phone.reload()
    phone.wait_for_selector("#recovery")
    expect(phone.locator("#recovery")).to_contain_text("Pooleli tagastus")
    phone.click("#recover-retry")
    expect(phone.locator("#refund-paid")).to_have_text("Välja 2,50 €")
    assert keys[0] == keys[1]
    d = drawer(server)
    assert (d["refund_count"], d["cash_refunds_cents"]) == (1, 250)   # one payout
    assert phone.evaluate(f"localStorage.getItem('{PENDING}')") is None


def test_definitive_rejection_clears_pending(phone, server):
    open_drawer(phone)
    add(phone, "Kohv")
    wait_quote(phone)
    # Close the drawer behind the UI's back: the sale is rejected with 409.
    reg = server.api("/api/v1/registers")[0]
    d = server.api(f"/api/v1/registers/{reg['id']}/drawer")["drawer"]
    import httpx
    httpx.post(f"{server.base}/api/v1/drawer/{d['id']}/close", json={"counted_cents": 5000},
               headers={"X-Tenant-ID": "demo", "X-Cashier-ID": "kassa1"}).raise_for_status()
    phone.click("#pay-button")
    phone.fill("#tender-input", "2,50")
    phone.click("#confirm-payment")
    expect(phone.locator(".pay-state.error")).to_contain_text("Makset ei salvestatud")
    assert phone.evaluate(f"localStorage.getItem('{PENDING}')") is None
    assert sales(server) == []


def test_corrupted_pending_record_blocks_until_manager_discards(phone, server):
    open_drawer(phone)
    phone.evaluate(f"localStorage.setItem('{PENDING}', '{{not json')")
    phone.reload()
    phone.wait_for_selector("#recovery")
    expect(phone.locator("#recovery")).to_contain_text("loetamatu")
    expect(phone.locator("#recover-retry")).to_have_count(0)   # nothing safe to retry
    phone.click("#recover-discard")
    expect(phone.locator("#modal")).to_be_visible()             # needs a second confirmation
    phone.click("#recover-discard-confirm")
    expect(phone.locator("#modal")).to_be_hidden()
    add(phone, "Kohv")
    pay(phone, "2,50")
    expect(phone.locator("#change-due")).to_be_visible()
    assert len(sales(server)) == 1


def test_unavailable_storage_blocks_payment(browser, server):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.add_init_script("Storage.prototype.setItem = function () { throw new Error('quota'); };")
    page = ctx.new_page()
    page.goto(f"{server.base}/one/")
    page.wait_for_selector("#product-grid .product")
    open_drawer(page)
    add(page, "Kohv")
    expect(page.locator("#total")).to_have_text("2,50 €")       # server quote arrived
    expect(page.locator("#quote-state")).to_contain_text("salvestus ei tööta")
    expect(page.locator("#pay-button")).to_be_disabled()        # still locked after quote
    assert sales(server) == []
    ctx.close()


def test_storage_failing_at_submit_refuses_to_send(phone, server):
    open_drawer(phone)
    add(phone, "Kohv")
    wait_quote(phone)
    phone.click("#pay-button")
    phone.fill("#tender-input", "2,50")
    posts = []
    phone.on("request", lambda r: posts.append(r) if r.method == "POST" and r.url.endswith("/api/v1/sales/cash") else None)
    phone.evaluate("() => { Storage.prototype.setItem = function () { throw new Error('quota'); }; }")
    phone.click("#confirm-payment")
    expect(phone.locator(".pay-state.error")).to_contain_text("salvestus ei tööta")
    assert posts == []                                            # nothing was sent
    assert sales(server) == []


def test_second_tab_sees_pending_and_cannot_double_charge(phone, server):
    open_drawer(phone)
    keys = drop_first_response(phone, "**/api/v1/sales/cash")
    add(phone, "Tee")
    pay(phone, "2,00")
    expect(phone.locator(".pay-state.unconfirmed")).to_be_visible()

    tab2 = phone.context.new_page()                     # same origin + storage
    tab2.goto(f"{server.base}/one/")
    tab2.wait_for_selector("#recovery")
    tab2.click("#recover-retry")
    expect(tab2.locator("#change-due")).to_have_text("Tagasi 0,00 €")

    phone.click("#confirm-payment")                     # first tab retries too: same key
    expect(phone.locator("#change-due")).to_have_text("Tagasi 0,00 €")
    assert len(sales(server)) == 1
    assert keys[0] == keys[1]
    tab2.close()
