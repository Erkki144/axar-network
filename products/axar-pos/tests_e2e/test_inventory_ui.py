"""Inventory screens end to end (#14, invoice-based flow): purchase invoice ->
post -> stock -> recipe with live cost -> POS sale -> automatic consumption
visible on the stock card. Real server with the in-process event worker."""

from __future__ import annotations

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
