"""Offline-first O1b (ADR-0012 §7): a till (edge node, its own SQLite DB)
sells without the central server, then syncs. Exactly once, in order, nothing
lost, bad entries quarantined - under network failure, lost responses,
replays, reordering, tampering and parallel pushes.

The central app is the regular test app (SQLite, or PostgreSQL with
AXAR_TEST_PG); the edge app is a second, independent app on its own SQLite."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from axar_pos import journal
from axar_pos.api import create_app
from axar_pos.config import Settings
from axar_pos.models import (AuthEvent, DeviceCursor, DeviceInbox, DeviceJournalEntry, Location, Product,
                             Register, Sale, SyncOutbox, Tenant)
from axar_pos.sync_agent import SyncAgent

from conftest import Api, Clock

DEVICE = "edge-till-1"
CENTRAL_H = {"X-Tenant-ID": "t-a", "X-Cashier-ID": "sync"}


@pytest.fixture
def edge(tmp_path, clock):
    """The till's own node: separate SQLite database, same catalog codes."""
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'edge.db'}", demo_identity=True,
                              clock=clock, edge_device_id=DEVICE))
    with app.state.session_factory.begin() as s:
        s.add(Tenant(id="t-a", name="Kohvik A"))
        s.flush()
        s.add_all([Product(tenant_id="t-a", sku="KOHV", name="Kohv", price_cents=250),
                   Product(tenant_id="t-a", sku="SAI", name="Saiake", price_cents=199)])
        loc = Location(tenant_id="t-a", code="L1", name="Kohvik A kesklinn")
        s.add(loc)
        s.flush()
        s.add(Register(tenant_id="t-a", location_id=loc.id, code="R1", name="Kassa 1"))
    with TestClient(app) as c:
        api = Api(c, "t-a")
        api.app = app
        yield api
    app.state.engine.dispose()


@pytest.fixture
def central(a, app):
    a.open_drawer(0)                                     # central R1 drawer open: sales can be applied
    return a


class Net:
    """The network between till and central, with fault injection."""

    def __init__(self, client):
        self.c, self.mode, self.calls = client, "up", 0

    def send(self, body):
        self.calls += 1
        if self.mode == "down":
            raise ConnectionError("no route to host")
        if self.mode == "server_error":
            return 503, None
        r = self.c.post("/api/v1/sync/push", json=body, headers=CENTRAL_H)
        if self.mode == "lose_response":
            raise TimeoutError("response lost after the server committed")
        return r.status_code, r.json()


def agent(edge, net, clock, **kw):
    return SyncAgent(edge.app.state.session_factory, "t-a", DEVICE, net.send, clock, **kw)


def sell_on_edge(edge, n, qty=1):
    for _ in range(n):
        r = edge.sell([("KOHV", qty)], 1000)
        assert r.status_code == 201, r.text


def central_sales(app):
    with app.state.session_factory() as s:
        return list(s.scalars(select(Sale).where(Sale.idempotency_key.like("edge:%"))))


def outbox(edge, status=None):
    with edge.app.state.session_factory() as s:
        q = select(SyncOutbox).order_by(SyncOutbox.seq)
        if status:
            q = q.where(SyncOutbox.status == status)
        return list(s.scalars(q))


# ------------------------------------------------------------ offline, then sync


def test_offline_sales_sync_exactly_once_after_the_network_returns(edge, central, app, clock):
    edge.open_drawer(0)
    sell_on_edge(edge, 20)
    assert len(outbox(edge, "pending")) == 20                       # written with each sale
    net = Net(central.c)
    net.mode = "down"
    ag = agent(edge, net, clock)
    r = ag.run_once()
    assert r.state == "retry" and len(outbox(edge, "pending")) == 20
    head = outbox(edge)[0]
    assert head.attempts == 1 and head.next_attempt_at > clock() and "no route" in head.last_error
    assert ag.run_once().state == "waiting"                          # backoff respected
    net.mode = "up"
    clock.at += timedelta(minutes=10)
    rounds = ag.run_until_idle()
    assert rounds[-1].state == "idle" and sum(x.acked for x in rounds) == 20
    sales = central_sales(app)
    assert len(sales) == 20 and sum(x.total_cents for x in sales) == 20 * 250
    with app.state.session_factory() as s:
        cur = s.get(DeviceCursor, ("t-a", DEVICE))
        assert cur.last_seq == 20
    assert ag.status()["pending"] == 0 and ag.status()["acked"] == 20


def test_lost_response_and_replayed_batches_never_duplicate(edge, central, app, clock):
    edge.open_drawer(0)
    sell_on_edge(edge, 5)
    net = Net(central.c)
    ag = agent(edge, net, clock)
    net.mode = "lose_response"                                       # server committed, till did not hear
    assert ag.run_once().state == "retry"
    assert len(central_sales(app)) == 5 and len(outbox(edge, "pending")) == 5
    net.mode = "up"
    clock.at += timedelta(minutes=10)
    ag.run_until_idle()
    assert len(central_sales(app)) == 5 and len(outbox(edge, "acked")) == 5
    # replay the whole stream once more by hand: all duplicates, nothing new
    with edge.app.state.session_factory() as s:
        rows = list(s.scalars(select(SyncOutbox).order_by(SyncOutbox.seq)))
        ents = {e.id: e for e in s.scalars(select(DeviceJournalEntry))}
        body = {"device_id": DEVICE, "entries": [{"entry": journal.entry_wire(ents[r.entry_id]), "payload": r.payload,
                                                  "payload_hash": r.payload_hash} for r in rows]}
    out = central.c.post("/api/v1/sync/push", json=body, headers=CENTRAL_H).json()
    assert {x["status"] for x in out["results"]} == {"duplicate"} and out["acked_through"] == 5
    assert len(central_sales(app)) == 5


def test_server_errors_are_retried_with_growing_backoff(edge, central, app, clock):
    edge.open_drawer(0)
    sell_on_edge(edge, 1)
    net = Net(central.c)
    net.mode = "server_error"
    ag = agent(edge, net, clock)
    delays = []
    for _ in range(4):
        assert ag.run_once().state == "retry"
        nxt = outbox(edge)[0].next_attempt_at
        delays.append((nxt - clock()).total_seconds())
        clock.at = nxt
    assert delays[0] < delays[1] < delays[2] < delays[3] <= 300
    net.mode = "up"
    assert ag.run_until_idle()[-1].state == "idle" and len(central_sales(app)) == 1


def _bodies(edge):
    with edge.app.state.session_factory() as s:
        rows = list(s.scalars(select(SyncOutbox).order_by(SyncOutbox.seq)))
        ents = {e.id: e for e in s.scalars(select(DeviceJournalEntry))}
        return [{"entry": journal.entry_wire(ents[r.entry_id]), "payload": r.payload,
                 "payload_hash": r.payload_hash} for r in rows]


def test_out_of_order_entries_are_not_applied_until_the_gap_is_filled(edge, central, app):
    edge.open_drawer(0)
    sell_on_edge(edge, 5)
    items = _bodies(edge)
    out = central.c.post("/api/v1/sync/push", json={"device_id": DEVICE, "entries": items[2:]},
                         headers=CENTRAL_H).json()
    assert out["results"][0]["status"] == "gap" and len(out["results"]) == 1 and out["acked_through"] == 0
    assert central_sales(app) == []
    out = central.c.post("/api/v1/sync/push", json={"device_id": DEVICE, "entries": items[::-1]},
                         headers=CENTRAL_H).json()                    # any order in one batch is sorted
    assert [x["status"] for x in out["results"]] == ["accepted"] * 5
    assert len(central_sales(app)) == 5


# ------------------------------------------------------------------ integrity


def test_tampered_payload_breaks_the_chain_and_blocks_the_agent(edge, central, app, clock):
    edge.open_drawer(0)
    sell_on_edge(edge, 3)
    with edge.app.state.session_factory() as s:                       # someone edits the queued sale
        row = s.scalars(select(SyncOutbox).where(SyncOutbox.seq == 2)).one()
        p = json.loads(row.payload)
        p["lines"][0][1] = 9
        row.payload = json.dumps(p)
        s.commit()
    ag = agent(edge, Net(central.c), clock)
    r = ag.run_once()
    assert r.state == "blocked" and "payload hash mismatch" in r.detail
    assert [x.status for x in outbox(edge)] == ["acked", "rejected", "pending"]
    assert len(central_sales(app)) == 1                               # nothing after the break
    assert ag.run_once().state == "blocked"                           # does not skip it
    with app.state.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(AuthEvent).where(AuthEvent.event == "sync.chain_broken")) == 1


def test_forged_entry_and_reused_seq_are_refused(edge, central, app):
    edge.open_drawer(0)
    sell_on_edge(edge, 2)
    items = _bodies(edge)
    central.c.post("/api/v1/sync/push", json={"device_id": DEVICE, "entries": items}, headers=CENTRAL_H)
    forged = json.loads(json.dumps(items[1]))
    forged["entry"]["hash"] = "a" * 64
    out = central.c.post("/api/v1/sync/push", json={"device_id": DEVICE, "entries": [forged]},
                         headers=CENTRAL_H).json()
    assert out["results"][0]["status"] == "conflict"
    nxt = json.loads(json.dumps(items[1]))
    nxt["entry"]["seq"] = 3                                          # hash no longer matches content
    out = central.c.post("/api/v1/sync/push", json={"device_id": DEVICE, "entries": [nxt]},
                         headers=CENTRAL_H).json()
    assert out["results"][0]["status"] == "chain_broken"
    assert len(central_sales(app)) == 2


# ----------------------------------------------------------------- quarantine


def test_unappliable_sales_are_quarantined_not_lost_then_retried_once(edge, a, app, clock):
    """Central drawer closed while the till sold offline: the entries are
    acknowledged as quarantined (stream moves on), then a manager opens the
    drawer and retries - each sale is recorded exactly once."""
    edge.open_drawer(0)
    sell_on_edge(edge, 3)
    ag = agent(edge, Net(a.c), clock)
    ag.run_until_idle()
    assert [x.status for x in outbox(edge)] == ["quarantined"] * 3
    assert central_sales(app) == []
    m = a.as_role("manager")
    q = m.get("/api/v1/sync/quarantine").json()
    assert len(q) == 3 and all("no_open_drawer" in x["reason"] for x in q)
    a.open_drawer(0)
    for x in q:
        r = m.post(f"/api/v1/sync/quarantine/{x['id']}/retry")
        assert r.status_code == 200 and r.json()["status"] == "resolved", r.text
    assert len(central_sales(app)) == 3
    assert m.post(f"/api/v1/sync/quarantine/{q[0]['id']}/retry").status_code == 409   # already resolved
    assert m.get("/api/v1/sync/quarantine").json() == []


def test_retry_never_sells_twice_even_if_the_sale_already_exists(edge, central, app, clock):
    """Defence in depth behind the row lock: the sale's idempotency key is the
    journal entry id, so a retry of an entry whose sale already exists (state
    repaired by hand, restored backup, ...) links to that sale, never a new one."""
    edge.open_drawer(0)
    sell_on_edge(edge, 1)
    agent(edge, Net(central.c), clock).run_until_idle()
    [sale] = central_sales(app)
    with app.state.session_factory.begin() as s:
        row = s.scalars(select(DeviceInbox)).one()
        assert sale.idempotency_key == f"edge:{row.entry_id}" and row.sale_id == sale.id
        row.status, row.sale_id = "quarantined", None              # inconsistent on purpose
    m = central.as_role("manager")
    [q] = m.get("/api/v1/sync/quarantine").json()
    clock.at += timedelta(minutes=5)
    r = m.post(f"/api/v1/sync/quarantine/{q['id']}/retry")
    assert r.status_code == 200 and r.json()["status"] == "resolved", r.text
    assert [x.id for x in central_sales(app)] == [sale.id]
    with app.state.session_factory() as s:
        assert s.scalars(select(DeviceInbox)).one().sale_id == sale.id


def test_price_mismatch_is_quarantined_and_can_be_dismissed_with_a_reason(edge, central, app, clock):
    with app.state.session_factory.begin() as s:                      # central price changed meanwhile
        s.scalars(select(Product).where(Product.tenant_id == "t-a", Product.sku == "KOHV")).one().price_cents = 300
    edge.open_drawer(0)
    sell_on_edge(edge, 1)
    agent(edge, Net(central.c), clock).run_until_idle()
    m = central.as_role("manager")
    [q] = m.get("/api/v1/sync/quarantine").json()
    assert q["reason"].startswith("price_mismatch: till charged 250, central prices give 300")
    assert m.post(f"/api/v1/sync/quarantine/{q['id']}/dismiss", {"note": "x"}).status_code == 422
    r = m.post(f"/api/v1/sync/quarantine/{q['id']}/dismiss", {"note": "recorded manually in the books"})
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    assert central_sales(app) == []
    with app.state.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(AuthEvent)
                        .where(AuthEvent.event == "sync.quarantine_dismissed")) == 1


def test_refunds_made_offline_are_quarantined_for_review(edge, central, app, clock):
    edge.open_drawer(10_000)
    sale = edge.sell([("KOHV", 2)], 1000).json()
    assert edge.as_role("manager").refund(sale["id"], [(1, 1)]).status_code == 201
    agent(edge, Net(central.c), clock).run_until_idle()
    assert [x.status for x in outbox(edge)] == ["acked", "quarantined"]
    [q] = central.as_role("manager").get("/api/v1/sync/quarantine").json()
    assert q["entry_type"] == "refund" and "not supported yet" in q["reason"]


# -------------------------------------------------------------------- the edge


def test_edge_journal_is_per_device_and_still_verifies(edge):
    edge.open_drawer(0)
    sell_on_edge(edge, 3)
    with edge.app.state.session_factory() as s:
        v = journal.verify(s, "t-a", f"device:{DEVICE}")
        assert v["ok"] and v["entries"] == 3
        assert {o.entry_id for o in s.scalars(select(SyncOutbox))} == \
            {e.id for e in s.scalars(select(DeviceJournalEntry))}           # one outbox row per entry


def test_a_failed_sale_on_the_edge_queues_nothing(edge):
    edge.open_drawer(0)
    assert edge.sell([("KOHV", 4)], 100).status_code == 422
    assert outbox(edge) == []


def test_two_agents_pushing_the_same_device_in_parallel_apply_each_sale_once(edge, central, app, clock):
    edge.open_drawer(0)
    sell_on_edge(edge, 12)
    items = _bodies(edge)

    def push(chunk):
        return central.c.post("/api/v1/sync/push", json={"device_id": DEVICE, "entries": chunk},
                              headers=CENTRAL_H).json()

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(push, [items] * 6))
    sales = central_sales(app)
    assert len(sales) == 12 and len({x.idempotency_key for x in sales}) == 12
    with app.state.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(DeviceInbox)) == 12
