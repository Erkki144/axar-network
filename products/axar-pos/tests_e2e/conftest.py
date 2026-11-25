"""Browser end-to-end tests for the AXAR ONE cashier UI (issue #11).

Each test gets a fresh uvicorn server with its own SQLite file and demo seed,
bound to 127.0.0.1 only. Run with:  python -m pytest tests_e2e
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"X-Tenant-ID": "demo", "X-Cashier-ID": "kassa1"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, tmp: Path, extra_env: dict | None = None, before_start=None):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        env = {**os.environ,
               "AXAR_POS_DATABASE_URL": f"sqlite:///{tmp / 'e2e.db'}",
               "AXAR_POS_DEMO_IDENTITY": "1", "AXAR_POS_SEED_DEMO": "1", **(extra_env or {})}
        env = {k: v for k, v in env.items() if v is not None}
        self.env = env
        if before_start:
            before_start(env)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "axar_pos.main:app",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=ROOT, env=env)
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if httpx.get(f"{self.base}/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("uvicorn did not start")

    def api(self, path, **kw):
        r = httpx.get(f"{self.base}{path}", headers=HEADERS, timeout=5, **kw)
        r.raise_for_status()
        return r.json()

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def server(tmp_path):
    srv = Server(tmp_path)
    yield srv
    srv.stop()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _page(browser, server, width, height):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.errors = errors  # type: ignore[attr-defined]
    page.goto(f"{server.base}/one/")
    page.wait_for_selector("#product-grid .product")
    return page


@pytest.fixture
def phone(browser, server):
    page = _page(browser, server, 390, 844)
    yield page
    assert page.errors == [], page.errors
    page.context.close()


@pytest.fixture
def tablet(browser, server):
    page = _page(browser, server, 1024, 768)
    yield page
    assert page.errors == [], page.errors
    page.context.close()
