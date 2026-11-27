"""Real browser click-through for the isolated, static AXAR ATLAS app."""
from pathlib import Path

from playwright.sync_api import expect

ATLAS = Path(__file__).resolve().parents[2] / "axar-one" / "atlas"


def test_atlas_graph_is_clickable_and_does_not_claim_live_data(browser):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto((ATLAS / "index.html").as_uri())
    expect(page.locator("#metric-nodes")).to_have_text("20")
    expect(page.locator("#metric-edges")).to_have_text("28")
    expect(page.locator(".node")).to_have_count(20)
    expect(page.locator(".edge")).to_have_count(28)
    expect(page.get_by_text("LIVE ÜHENDAMATA")).to_be_visible()
    assert page.evaluate("window.AXAR_ATLAS_TEST.hasLiveData") is False

    page.locator('.node[data-id="stock"]').dispatch_event("click")
    expect(page.locator("#inspector-body .detail-title")).to_have_text("STOCK")
    expect(page.locator("#inspector-body .source-list")).to_contain_text("inventory.py")
    expect(page.locator("#inspector-body .issue-tags")).to_contain_text("#69")

    page.fill("#graph-search", "POSTGRESQL")
    expect(page.locator('.node[data-id="db"]')).not_to_have_class("node dim")
    expect(page.locator('.node[data-id="pos"]')).to_have_class("node dim")

    page.fill("#graph-search", "")
    page.select_option("#edge-filter", "event")
    expect(page.locator(".edge:not(.dim)")).not_to_have_count(0)
    page.select_option("#edge-filter", "all")

    page.click("#zoom-in")
    expect(page.locator("#zoom-fit")).to_have_text("120%")
    page.click("#zoom-fit")
    expect(page.locator("#zoom-fit")).to_have_text("100%")

    page.locator('.nav-btn[data-view="journeys"]').click()
    expect(page.locator(".journey-card")).to_have_count(4)
    page.locator(".journey-card").first.get_by_role("button", name="Ava arhitektuurikaardil").click()
    expect(page.locator("#architecture-view")).to_be_visible()
    expect(page.locator(".node.dim")).not_to_have_count(0)
    expect(page.locator("#inspector-body")).to_contain_text("MITTE LIVE TEHING")

    page.locator('.nav-btn[data-view="registers"]').click()
    expect(page.locator("#registry-table tr")).to_have_count(20)
    page.locator('.nav-btn[data-view="gates"]').click()
    expect(page.locator(".gate-card")).to_have_count(8)
    assert errors == [], errors
    page.close()


def test_atlas_phone_layout_and_keyboard_inspection(browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto((ATLAS / "index.html").as_uri())
    expect(page.locator("#network")).to_be_visible()
    page.locator('.node[data-id="api"]').focus()
    page.keyboard.press("Enter")
    expect(page.locator("#inspector-body .detail-title")).to_have_text("FASTAPI")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "horizontal overflow"
    page.close()
