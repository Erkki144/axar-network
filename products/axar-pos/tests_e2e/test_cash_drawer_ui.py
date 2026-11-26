"""#68 real browser + API test: cashier sees cash in/out and posts immutable movements."""
from playwright.sync_api import expect


def test_cash_in_out_buttons_update_only_physical_cash(phone, server):
    phone.click("#open-drawer")
    phone.fill("#float-input", "100,00")
    phone.click("#confirm-open-drawer")
    expect(phone.locator("#cash-in")).to_be_visible()
    expect(phone.locator("#cash-out")).to_be_visible()

    phone.click("#cash-in")
    phone.fill("#cash-move-amount", "20,00")
    phone.fill("#cash-move-reason", "Vahetusraha täiendamine")
    expect(phone.locator("#confirm-cash-movement")).to_be_enabled()
    phone.click("#confirm-cash-movement")
    expect(phone.locator("#dialog-title")).to_have_text("Kassaliikumine kinnitatud")
    phone.get_by_role("button", name="Valmis").click()
    expect(phone.locator("#drawer-status")).to_contain_text("120,00 €")

    phone.click("#cash-out")
    phone.fill("#cash-move-amount", "40,00")
    phone.fill("#cash-move-reason", "Seifi üleandmine")
    phone.click("#confirm-cash-movement")
    expect(phone.locator("#dialog-title")).to_have_text("Kassaliikumine kinnitatud")
    phone.get_by_role("button", name="Valmis").click()
    expect(phone.locator("#drawer-status")).to_contain_text("80,00 €")

    rid = server.api("/api/v1/registers")[0]["id"]
    d = server.api(f"/api/v1/registers/{rid}/drawer")["drawer"]
    assert d["cash_in_cents"] == 2000
    assert d["cash_out_cents"] == 4000
    assert d["expected_cents"] == 8000
    assert server.api("/api/v1/reports/daily")["total_cents"] == 0


def test_unknown_cash_out_reply_retries_same_command(phone, server):
    phone.click("#open-drawer")
    phone.fill("#float-input", "100,00")
    phone.click("#confirm-open-drawer")
    requests = []

    def drop_once(route):
        requests.append(route.request.headers.get("idempotency-key"))
        if len(requests) == 1:
            route.fetch()  # Server commits; caller loses the response.
            route.abort("connectionreset")
        else:
            route.continue_()

    phone.route("**/api/v1/drawer/*/movements", drop_once)
    phone.click("#cash-out")
    phone.fill("#cash-move-amount", "20,00")
    phone.fill("#cash-move-reason", "Seifi üleandmine")
    phone.click("#confirm-cash-movement")
    expect(phone.locator("#modal-content")).to_contain_text("tulemus pole teada")
    phone.click("#confirm-cash-movement")   # Must re-send same key, NOT pay twice.
    expect(phone.locator("#dialog-title")).to_have_text("Kassaliikumine kinnitatud")
    assert len(requests) == 2 and requests[0] == requests[1]
    rid = server.api("/api/v1/registers")[0]["id"]
    assert server.api(f"/api/v1/registers/{rid}/drawer")["drawer"]["expected_cents"] == 8000
