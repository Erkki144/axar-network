"""C3 (#15) end to end in the browser: a table bill of €85 paid as €20 cash +
€45 card (DEMO terminal) + €20 bank transfer; lost card answer reconciled;
lost response of a tender recovered with the same key after a reload."""

from __future__ import annotations

from playwright.sync_api import expect

from test_cashier_ui import add, open_drawer, sales, wait_quote

PENDING = "axar.one.pending.v1"


def table_with(page, table, product, n):
    page.click('[data-mode="tables"]')
    page.locator("#table-grid .table-card", has_text=table).click()
    expect(page.locator("#basket-title")).to_contain_text(table)
    add(page, product, n)
    expect(page.locator("#basket-count")).to_have_text(str(n))
    wait_quote(page)


def tender(page, method, amount, **kw):
    page.click(f"#method-{method}")
    page.fill("#bill-amount", amount)
    if method == "cash":
        page.fill("#bill-tendered", kw.get("tendered", amount))
    if method == "card":
        page.select_option("#bill-simulate", kw.get("simulate", "approved"))
    if method == "transfer":
        page.fill("#bill-customer", kw["customer"])
    page.click("#bill-confirm")


def test_85_euro_bill_split_three_ways(phone, server):
    open_drawer(phone)
    table_with(phone, "Laud 01", "Kohv", 34)                     # 34 x 2,50 = 85,00
    phone.click("#split-button")
    expect(phone.locator("#bill-total")).to_have_text("85,00 €")
    expect(phone.locator("#bill-remaining")).to_have_text("85,00 €")

    tender(phone, "cash", "20,00", tendered="50,00")
    expect(phone.locator("#bill-paid")).to_have_text("20,00 €")
    expect(phone.locator("#bill-remaining")).to_have_text("65,00 €")
    expect(phone.locator("#bill-payments")).to_contain_text("Sularaha 20,00 €")

    tender(phone, "card", "45,00")
    expect(phone.locator("#bill-remaining")).to_have_text("20,00 €")
    expect(phone.locator("#bill-payments")).to_contain_text("Kaart 45,00 €")

    tender(phone, "transfer", "20,00", customer="OÜ Näide")
    expect(phone.locator("#receipt")).to_contain_text("85,00")      # ONE receipt for the whole bill
    expect(phone.locator(".pay-state.unconfirmed")).to_contain_text("ootab panka")
    phone.click("#next-customer")
    phone.click('[data-mode="tables"]')
    expect(phone.locator("#table-grid .table-card", has_text="Laud 01")).to_contain_text("Vaba")

    s = sales(server)
    assert len(s) == 1 and s[0]["total_cents"] == 8500 and s[0]["payment_method"] == "mixed"
    reg = server.api("/api/v1/registers")[0]
    drawer = server.api(f"/api/v1/registers/{reg['id']}/drawer")["drawer"]
    assert drawer["expected_cents"] == 5000 + 2000                  # only the cash is in the till
    assert phone.evaluate(f"localStorage.getItem('{PENDING}')") is None


def test_partially_paid_bill_is_marked_and_continued(phone, server):
    open_drawer(phone)
    table_with(phone, "Laud 02", "Kohv", 4)                       # 10,00
    phone.click("#split-button")
    tender(phone, "card", "4,00")
    expect(phone.locator("#bill-remaining")).to_have_text("6,00 €")
    phone.click("#close-modal")
    expect(phone.locator("#table-grid .table-card", has_text="Laud 02")).to_contain_text("Osaliselt tasutud")
    expect(phone.locator("#pay-button")).to_contain_text("Maksa ülejäänu")
    add(phone, "Kohv")                                            # lines are frozen after money was taken
    expect(phone.locator("#order-message")).to_contain_text("osaliselt tasutud")
    phone.click("#pay-button")                                    # continues in the split dialog
    expect(phone.locator("#bill-remaining")).to_have_text("6,00 €")
    tender(phone, "cash", "6,00", tendered="10,00")
    expect(phone.locator("#change-due")).to_have_text("Tagasi 4,00 €")
    assert len(sales(server)) == 1


def test_declined_card_and_lost_card_answer_reconciled(phone, server):
    open_drawer(phone)
    table_with(phone, "Laud 03", "Kohv", 2)                       # 5,00
    phone.click("#split-button")
    tender(phone, "card", "5,00", simulate="declined")
    expect(phone.locator(".pay-state.error")).to_contain_text("kaart keeldus")
    expect(phone.locator("#bill-remaining")).to_have_text("5,00 €")
    tender(phone, "card", "5,00", simulate="timeout")
    expect(phone.locator("#bill-inflight")).to_have_text("5,00 €")
    expect(phone.locator(".pay-state.unconfirmed")).to_contain_text("Küsi terminalist")
    assert sales(server) == []                                     # not settled while unknown
    phone.locator("[data-reconcile]").click()
    expect(phone.locator("#receipt")).to_contain_text("5,00")
    assert len(sales(server)) == 1


def test_lost_tender_answer_recovers_once_after_reload(phone, server):
    open_drawer(phone)
    table_with(phone, "Laud 04", "Kohv", 4)                       # 10,00
    phone.click("#split-button")
    calls = []

    def lose_answer(route):
        calls.append(route.request.headers.get("idempotency-key"))
        route.fetch(); route.abort("connectionreset")             # committed, answer lost

    phone.route("**/api/v1/orders/*/payments", lose_answer)
    tender(phone, "card", "4,00")
    expect(phone.locator(".pay-state.unconfirmed")).to_be_visible()
    assert phone.evaluate(f"localStorage.getItem('{PENDING}')") is not None
    phone.unroute("**/api/v1/orders/*/payments")
    phone.reload()
    phone.wait_for_selector("#recovery")
    expect(phone.locator("#recovery")).to_contain_text("Lauaarve osamakse: Kaart 4,00 €")
    expect(phone.locator("#recovery-server-status")).to_contain_text("on see tehing salvestatud")
    phone.click("#recover-retry")                                  # same key -> replay, not a 2nd payment
    expect(phone.locator("#bill-remaining")).to_have_text("6,00 €")
    expect(phone.locator(".pay-state.ok")).to_contain_text("Pooleli makse lõpetati")
    assert phone.evaluate(f"localStorage.getItem('{PENDING}')") is None
    assert len(calls) == 1
