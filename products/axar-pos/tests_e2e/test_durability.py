"""Server process killed (SIGKILL) while several clients sell concurrently.

What this proves (SQLite, one local uvicorn process):
- every sale the client got a 201 for is still there after restart;
- retrying every key that was sent (including ones whose answer was lost)
  records each sale exactly once — no duplicates, nothing lost;
- receipt numbers stay gap-free.

What it does NOT prove: an OS power cut or disk failure (no fsync fault
injection here) — see the readiness audit, E18.
"""

from __future__ import annotations

import os
import signal
import threading
import time
import uuid

import httpx

import pytest

from conftest import HEADERS, Server

# kill -9 needs POSIX signals; CI (Linux) always runs this test.
pytestmark = pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="POSIX-only: needs signal.SIGKILL")


def _sell(client: httpx.Client, reg: int, pid: int, key: str) -> httpx.Response:
    return client.post("/api/v1/sales/cash", headers={"Idempotency-Key": key},
                       json={"register_id": reg, "lines": [{"product_id": pid, "quantity": 1}],
                             "tendered_cents": 1000})


def test_kill9_during_concurrent_sales_loses_nothing_and_duplicates_nothing(tmp_path):
    srv = Server(tmp_path)
    reg = srv.api("/api/v1/registers")[0]["id"]
    pid = srv.api("/api/v1/products")[0]["id"]
    httpx.post(f"{srv.base}/api/v1/drawer/open", headers=HEADERS,
               json={"register_id": reg, "opening_float_cents": 0}).raise_for_status()

    acked: dict[str, str] = {}
    sent: list[str] = []
    lock = threading.Lock()
    stop = threading.Event()

    def worker():
        with httpx.Client(base_url=srv.base, headers=HEADERS, timeout=3) as c:
            while not stop.is_set():
                key = str(uuid.uuid4())
                with lock:
                    sent.append(key)
                try:
                    r = _sell(c, reg, pid, key)
                except httpx.HTTPError:
                    continue  # answer lost: outcome unknown, retried below
                if r.status_code == 201:
                    with lock:
                        acked[key] = r.json()["id"]

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    deadline = time.time() + 10
    while len(acked) < 30 and time.time() < deadline:
        time.sleep(0.05)
    os.kill(srv.proc.pid, signal.SIGKILL)
    srv.proc.wait()
    stop.set()
    for t in threads:
        t.join()
    assert len(acked) >= 30, "not enough load before the kill"

    srv2 = Server(tmp_path)  # same database file
    try:
        with httpx.Client(base_url=srv2.base, headers=HEADERS, timeout=5) as c:
            missing = [k for k, sid in acked.items() if c.get(f"/api/v1/sales/{sid}").status_code != 200]
            assert missing == []
            for key in sent:  # same-key retry of everything, as a till would after a crash
                r = _sell(c, reg, pid, key)
                assert r.status_code in (200, 201), r.text
                if key in acked:
                    assert r.status_code == 200 and r.json()["id"] == acked[key]
            sales = c.get("/api/v1/sales").json()
        assert len(sales) == len(sent)
        assert len({s["id"] for s in sales}) == len(sent)
        assert sorted(s["receipt_no"] for s in sales) == list(range(1, len(sent) + 1))
    finally:
        srv2.stop()
