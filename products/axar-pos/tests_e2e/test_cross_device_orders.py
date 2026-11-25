"""Slice 4: table bills and kitchen tickets shared between DEVICES.

Each device is a separate browser context: its own localStorage and its own
Web Locks, so nothing on the client can coordinate them — only the server's
order version (compare-and-swap) and the one-transaction "pay + close" do.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from test_cashier_ui import add, open_drawer, sales, wait_quote


@pytest.fixture
def device(browser, server):
    pages = []

    def make(block_stream=False):
        ctx = browser.new_context(viewport={"width": 1024, "height": 768})
        page = ctx.new_page()
        if block_stream:  # this device's realtime stream is down (it falls back to polling)
            page.route("**/api/v1/events/stream*", lambda route: route.abort())
        page.errors = []  # type: ignore[attr-defined]
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.goto(f"{server.base}/one/")
        page.wait_for_selector("#product-grid .product")
        pages.append(page)
        return page

    yield make
    for p in pages:
        assert p.errors == [], p.errors
        p.context.close()


def freeze_polling(page):
    """Simulate a backgrounded device: the UI skips polling while hidden."""
    page.evaluate("() => Object.defineProperty(document, 'hidden', { get: () => true, configurable: true })")


def open_table(page, table):
    page.click('[data-mode="tables"]')
    page.locator("#table-grid .table-card", has_text=table).click()
    expect(page.locator("#basket-title")).to_contain_text(table)


def test_bill_started_on_one_device_is_seen_and_extended_on_another(device, server):
    a, b = device(), device()
    open_table(a, "Laud 01")
    add(a, "Kohv", 2)
    expect(a.locator("#basket-count")).to_have_text("2")
    open_table(b, "Laud 01")
    expect(b.locator("#basket-count")).to_have_text("2")        # same server bill
    add(b, "Kaneelisaiake")
    expect(b.locator("#basket-count")).to_have_text("3")
    # Device A picks the change up by polling, without a reload.
    expect(a.locator("#basket-count")).to_have_text("3", timeout=10_000)
    expect(a.locator("#table-grid .table-card", has_text="Laud 01")).to_contain_text("3 tk")


def test_stale_device_cannot_overwrite_and_is_told(device, server):
    a, b = device(block_stream=True), device()
    open_table(a, "Laud 02")
    add(a, "Kohv")
    expect(a.locator("#basket-count")).to_have_text("1")
    open_table(b, "Laud 02")
    expect(b.locator("#basket-count")).to_have_text("1")
    freeze_polling(a)                                           # A keeps its old view (no stream, no poll)
    add(b, "Latte")
    expect(b.locator("#basket-count")).to_have_text("2")
    add(a, "Tee")                                               # based on version B already replaced
    expect(a.locator("#order-message")).to_contain_text("teises seadmes")
    expect(a.locator("#basket-count")).to_have_text("2")        # reloaded: Kohv + Latte, no Tee
    order = server.api("/api/v1/tables")
    bill = next(t for t in order if t["name"] == "Laud 02")["open_order"]
    assert bill["item_count"] == 2


def test_two_devices_pay_same_bill_only_one_sale(device, server):
    a = device()
    open_drawer(a)
    b = device()                                                # same register, booted after opening
    open_table(a, "Laud 03")
    add(a, "Päevasupp", 2)
    expect(a.locator("#basket-count")).to_have_text("2")
    open_table(b, "Laud 03")
    expect(b.locator("#basket-count")).to_have_text("2")
    for p in (a, b):
        wait_quote(p)
        p.click("#pay-button")
        p.fill("#tender-input", "20,00")
    at = a.evaluate("Date.now() + 500")
    for p in (a, b):
        p.evaluate("(t) => setTimeout(() => document.getElementById('confirm-payment').click(), t - Date.now())", at)
    for p in (a, b):
        expect(p.locator("#change-due, .pay-state.error").first).to_be_visible(timeout=10_000)
    assert len(sales(server)) == 1
    done = [p for p in (a, b) if p.locator("#change-due").count()]
    lost = [p for p in (a, b) if p.locator(".pay-state.error").count()]
    assert len(done) == 1 and len(lost) == 1
    expect(lost[0].locator(".pay-state.error")).to_contain_text("teises seadmes")
    # The loser's journal was cleared (proven not recorded): it is not blocked.
    assert lost[0].evaluate("localStorage.getItem('axar.one.pending.v1')") is None
    reg = server.api("/api/v1/registers")[0]
    assert server.api(f"/api/v1/registers/{reg['id']}/drawer")["drawer"]["sale_count"] == 1


def test_kitchen_ticket_from_waiter_device_appears_in_kitchen_device(device, server):
    waiter, kitchen = device(), device()
    kitchen.click('[data-mode="kitchen"]')
    expect(kitchen.locator("#kds-grid")).to_contain_text("Köögipileteid pole")
    open_table(waiter, "Terrass 01")
    add(waiter, "Päevasupp", 2)
    expect(waiter.locator("#basket-count")).to_have_text("2")
    waiter.click("#send-button")
    expect(waiter.locator("#order-message")).to_contain_text("Köögipilet #1 saadetud")
    expect(waiter.locator("#send-button")).to_contain_text("kööki saadetud")
    ticket = kitchen.locator(".ticket", has_text="Terrass 01")
    expect(ticket).to_contain_text("2 × Päevasupp", timeout=10_000)   # by polling
    ticket.locator("button").click()
    expect(ticket).to_contain_text("VALMIS")
    assert server.api("/api/v1/kitchen/tickets")[0]["status"] == "ready"
    # Already-sent quantity cannot be reduced from the waiter device.
    expect(waiter.locator('#basket-items button[aria-label="Vähenda: Päevasupp"]')).to_be_disabled()


def test_lost_kitchen_answer_retry_does_not_duplicate_ticket(device, server):
    waiter = device()
    open_table(waiter, "Terrass 02")
    add(waiter, "Kohv")
    expect(waiter.locator("#basket-count")).to_have_text("1")
    keys = []

    def lose(route):
        keys.append(route.request.headers.get("idempotency-key"))
        if len(keys) == 1:
            route.fetch(); route.abort("connectionreset")        # created, answer lost
        else:
            route.continue_()

    freeze_polling(waiter)                                      # retry uses the same view
    waiter.route("**/api/v1/orders/*/kitchen", lose)
    waiter.click("#send-button")
    expect(waiter.locator("#order-message")).to_contain_text("topeltpiletit ei teki")
    waiter.click("#send-button")
    expect(waiter.locator("#order-message")).to_contain_text("saadetud")
    assert len(keys) == 2 and keys[0] == keys[1]
    assert len(server.api("/api/v1/kitchen/tickets")) == 1


def test_realtime_stream_updates_other_device_without_polling(device, server):
    """Owner requirement: cross-device realtime sync. The kitchen device does
    not poll (hidden); the ticket arrives through the server-sent event feed."""
    import time

    waiter, kitchen = device(), device()
    kitchen.click('[data-mode="kitchen"]')
    expect(kitchen.locator("#net-status")).to_contain_text("reaalajas")
    freeze_polling(kitchen)
    open_table(waiter, "Terrass 03")
    add(waiter, "Latte", 1)
    expect(waiter.locator("#basket-count")).to_have_text("1")
    t0 = time.monotonic()
    waiter.click("#send-button")
    expect(kitchen.locator(".ticket", has_text="Terrass 03")).to_contain_text("1 × Latte", timeout=5_000)
    latency = time.monotonic() - t0
    assert latency < 3.0, latency
    from test_cashier_ui import record
    record("realtime_kitchen_ticket_latency_s", value=round(latency, 3))
    # Bill changes reach the other waiter device the same way.
    other = device()
    open_table(other, "Terrass 03")
    freeze_polling(other)
    add(waiter, "Tee", 1)
    expect(other.locator("#basket-count")).to_have_text("2", timeout=5_000)


def test_live_marker_survives_ordinary_api_calls(device):
    """Regression: every successful API call refreshed the status pill and
    dropped "· reaalajas" although the stream was still connected (seen as an
    intermittent failure of the realtime test above)."""
    kitchen = device()
    kitchen.click('[data-mode="kitchen"]')
    pill = kitchen.locator("#net-status")
    expect(pill).to_contain_text("reaalajas")
    expect(pill).to_have_attribute("data-live", "1")
    with kitchen.expect_response(lambda r: "/api/v1/" in r.url and "/events/" not in r.url and r.ok):
        kitchen.click('[data-mode="tables"]')                  # loads tables through the normal API
    kitchen.wait_for_timeout(200)
    expect(pill).to_contain_text("reaalajas")
