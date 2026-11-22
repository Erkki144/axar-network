"""End-to-end browser smoke, run in GitHub Actions where localhost is allowed.

Environment policy may block browser navigation in ChatGPT sandbox; do not
count local failures due to network policy as product failures, or as passes.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "prototype"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        pass


def run():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for width, height in [(1440, 900), (390, 844)]:
                page = browser.new_page(viewport={"width": width, "height": height})
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                assert page.locator("[data-product='kohv']").count() == 1
                page.locator("[data-product='kohv']").click()
                assert page.locator("#basket-count").inner_text() == "1"
                page.locator("#pay-button").click()
                page.locator("#tender-input").fill("10,00")
                page.locator("#modal .primary-btn").click()
                assert "7,50 €" in page.locator(".receipt").inner_text()
                page.get_by_text("Järgmine klient").click()
                page.locator("[data-mode='tables']").click()
                page.get_by_role("button", name="Laud 02").click()
                page.locator("[data-product='saiake']").click()
                page.locator("#send-button").click()
                assert page.locator("#basket-count").inner_text() == "1", "table should retain unpaid bill"
                assert page.locator("#send-button").is_disabled(), "same item must not fire twice"
                page.locator("[data-mode='kitchen']").click()
                assert page.get_by_text("Laud 02").count() >= 1
                page.locator("[data-mode='manager']").click()
                assert page.get_by_text("Näidiskäive").count() == 1
                page.locator("[data-mode='express']").click()
                widths = page.evaluate("({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})")
                assert widths["scroll"] <= widths["client"], f"horizontal overflow {widths}"
                targets = page.locator("[data-product='kohv']").bounding_box()
                assert targets and targets["width"] >= 48 and targets["height"] >= 48
                assert errors == [], errors
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
    print("AXAR EXPERIENCE browser smoke: desktop + mobile passed")
