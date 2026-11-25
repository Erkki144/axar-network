"""Inventory screens end to end (#14, invoice-based flow): purchase invoice ->
post -> stock -> recipe with live cost -> POS sale -> automatic consumption
visible on the stock card. Real server with the in-process event worker."""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import expect

from conftest import Server
from test_cashier_ui import add, open_drawer, pay


@pytest.fixture
def page(browser, tmp_path):
    srv = Server(tmp_path, {"AXAR_POS_INPROCESS_WORKER": "1"})
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    p = ctx.new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.goto(f"{srv.base}/one/")
    p.wait_for_selector("#product-grid .product")
    p.srv = srv
    yield p
    assert errors == [], errors
    ctx.close()
    srv.stop()


def test_invoice_to_stock_to_recipe_to_sale(page):
    page.click('[data-mode="inventory"]')
    # 1) purchase invoice with a NEW supplier and a NEW item, saved as draft
    page.click("#new-invoice")
    page.select_option("#inv-supplier", "new")
    page.fill("#inv-new-supplier", "Talu OÜ")
    page.fill("#inv-no", "A-1001")
    line = page.locator(".inv-line").first
    line.locator(".inv-item").select_option("new")
    line.locator(".inv-new-item").fill("Kartul")
    line.locator(".inv-qty").fill("10")
    line.locator(".inv-net").fill("50,00")
    page.click("#inv-save")
    row = page.locator("tr[data-doc='A-1001']")
    expect(row).to_contain_text("Mustand")
    expect(row).to_contain_text("62,00 €")                          # server: 50 + 24 % VAT
    page.locator('[data-tab="stock"]').click()
    expect(page.locator("#inv-body")).to_contain_text("Laos pole veel midagi")   # a draft moves nothing
    # 2) post it
    page.locator('[data-tab="invoices"]').click()
    page.locator("tr[data-doc='A-1001'] .inv-post").click()
    expect(page.locator("tr[data-doc='A-1001']")).to_contain_text("Kinnitatud")
    # duplicate number is refused
    page.click("#new-invoice")
    page.select_option("#inv-supplier", label="Talu OÜ")
    page.fill("#inv-no", "A-1001")
    page.locator(".inv-line .inv-item").first.select_option(label="Kartul (kg)")
    page.locator(".inv-line .inv-qty").first.fill("1")
    page.locator(".inv-line .inv-net").first.fill("5,00")
    page.click("#inv-save")
    expect(page.locator(".inv-form .dialog-error")).to_contain_text("juba olemas")
    # 3) stock
    page.locator('[data-tab="stock"]').click()
    potato = page.locator("tr[data-item^='KARTUL']")
    expect(potato).to_contain_text("10 kg")
    expect(potato).to_contain_text("50,00 €")
    expect(potato).to_contain_text("5,00 €/kg")
    # 4) recipe with live cost: Päevasupp 0,2 kg potato
    page.locator('[data-tab="recipes"]').click()
    page.locator("button.inv-offer[data-offer='Päevasupp']").click()
    rl = page.locator(".recipe-line").first
    rl.locator("select").select_option(label="Kartul (kg)")
    rl.locator("input").fill("0,2")
    expect(page.locator("#cost-per-portion")).to_have_text("1,00 €")
    expect(page.locator("#price-net")).to_have_text("4,76 €")          # 5,90 incl. 24 % VAT
    expect(page.locator("#margin")).to_have_text("3,76 €")
    expect(page.locator("#food-cost")).to_contain_text("21")
    page.click("#recipe-save")
    expect(page.locator("#recipe-editor")).to_contain_text("Kehtiv versioon 1")
    # 5) sell 2 portions at the till
    page.click('[data-mode="express"]')
    open_drawer(page)
    add(page, "Päevasupp", 2)
    pay(page, "20,00")
    expect(page.locator("#change-due")).to_be_visible()
    page.click("#next-customer")
    # 6) consumption arrives on the stock card (in-process worker, no manual step)
    page.click('[data-mode="inventory"]')
    page.locator('[data-tab="stock"]').click()
    expect(page.locator("tr[data-item^='KARTUL']")).to_contain_text("9,6 kg", timeout=10_000)
    page.locator("tr[data-item^='KARTUL'] button").click()
    sale = page.locator("tr[data-kind='sale_consumption']")
    expect(sale).to_contain_text("Müük")
    expect(sale).to_contain_text("2,00 €")                            # COGS of 0,4 kg


def _post_invoice(p, doc_no, qty, net, new_item=False):
    p.click('[data-mode="inventory"]')
    p.locator('[data-tab="invoices"]').click()
    p.click("#new-invoice")
    if new_item:
        p.select_option("#inv-supplier", "new")
        p.fill("#inv-new-supplier", "Talu OÜ")
    else:
        p.select_option("#inv-supplier", label="Talu OÜ")
    p.fill("#inv-no", doc_no)
    line = p.locator(".inv-line").first
    if new_item:
        line.locator(".inv-item").select_option("new")
        line.locator(".inv-new-item").fill("Kartul")
    else:
        line.locator(".inv-item").select_option(label="Kartul (kg)")
    line.locator(".inv-qty").fill(qty)
    line.locator(".inv-net").fill(net)
    p.click("#inv-save")
    p.locator(f"tr[data-doc='{doc_no}'] .inv-post").click()
    expect(p.locator(f"tr[data-doc='{doc_no}']")).to_contain_text("Kinnitatud")


def test_stock_card_redrawn_by_click_and_live_event_at_once_shows_one_table(page, browser):
    """Regression (seen once in CI): the stock card cleared the view, awaited the
    server, then appended. A click and a live 'stock changed' event arriving
    together drew the card twice (two tables, every movement shown twice)."""
    _post_invoice(page, "A-1", "10", "50,00", new_item=True)
    page.locator('[data-tab="stock"]').click()
    expect(page.locator("tr[data-item^='KARTUL']")).to_contain_text("10 kg")

    held = []
    page.route("**/api/v1/inventory/items/*/card", lambda route: held.append(route))
    page.locator("tr[data-item^='KARTUL'] button").click()           # render 1: waits for the server
    deadline = time.monotonic() + 5
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert len(held) == 1

    other_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    other = other_ctx.new_page()
    other.goto(f"{page.srv.base}/one/")
    other.wait_for_selector("#product-grid .product")
    _post_invoice(other, "A-2", "1", "5,00")                           # another device: stock changes
    while len(held) < 2 and time.monotonic() < deadline + 10:
        page.wait_for_timeout(50)                                       # render 2 from the live event
    assert len(held) == 2, "the live event did not redraw the open card"
    for route in held:                                                  # answers arrive together
        route.continue_()
    page.unroute("**/api/v1/inventory/items/*/card")
    expect(page.locator("tr[data-kind='receipt']")).to_have_count(2)    # A-1 and A-2, once each
    expect(page.locator("table.inv-card")).to_have_count(1)
    other_ctx.close()
