"""O3: refunds made on an offline till sync to the central server.

The central server applies the refund to ITS copy of the original sale (found
through the sale's journal entry) with idempotency key ``edge:<refund entry>``.
A refund is never applied twice; anything that does not match goes to the
manager's review queue and can be retried once the cause is fixed."""

import json

from sqlalchemy import select

from axar_pos.models import DeviceInbox, Refund, Sale, SyncOutbox

from test_offline_sync import CENTRAL_H, Net, _bodies, agent, central, central_sales, edge, outbox  # noqa: F401


def central_refunds(app):
    with app.state.session_factory() as s:
        return list(s.scalars(select(Refund).where(Refund.idempotency_key.like("edge:%"))))


def test_offline_sale_and_partial_refunds_sync_exactly_once(edge, central, app, clock):
    edge.open_drawer(10_000)
    sale = edge.sell([("KOHV", 3), ("SAI", 1)], 2000).json()
    m = edge.as_role("manager")
    assert m.refund(sale["id"], [(1, 1)]).status_code == 201                 # 1 coffee
    assert m.refund(sale["id"], [(1, 1), (2, 1)]).status_code == 201         # 1 coffee + the bun
    net = Net(central.c)
    net.mode = "lose_response"                                               # server applies, till never hears
    agent(edge, net, clock).run_once()
    net.mode = "up"
    clock.at += __import__("datetime").timedelta(minutes=10)
    agent(edge, net, clock).run_until_idle()
    assert [x.status for x in outbox(edge)] == ["acked", "acked", "acked"]
    [csale] = central_sales(app)
    refunds = central_refunds(app)
    assert len(refunds) == 2 and {r.original_sale_id for r in refunds} == {csale.id}
    assert sorted(r.total_cents for r in refunds) == [250, 250 + 199]
    with edge.app.state.session_factory() as s:                              # same totals as on the till
        assert sorted(r.total_cents for r in s.scalars(select(Refund))) == [250, 449]
    # the whole stream once more: duplicates only, no third refund
    out = central.c.post("/api/v1/sync/push", json={"device_id": "edge-till-1", "entries": _bodies(edge)},
                         headers=CENTRAL_H).json()
    assert {r["status"] for r in out["results"]} == {"duplicate"}
    assert len(central_refunds(app)) == 2


def test_refund_waits_for_its_quarantined_sale_and_applies_once_after_retry(edge, a, app, clock):
    """Central drawer closed: the sale is quarantined, so its refund is too.
    A manager opens the drawer and retries the sale, then the refund."""
    edge.open_drawer(10_000)
    sale = edge.sell([("KOHV", 2)], 1000).json()
    assert edge.as_role("manager").refund(sale["id"], [(1, 1)]).status_code == 201
    agent(edge, Net(a.c), clock).run_until_idle()
    m = a.as_role("manager")
    q = {x["entry_type"]: x for x in m.get("/api/v1/sync/quarantine").json()}
    assert "no_open_drawer" in q["sale"]["reason"]
    assert "original sale is quarantined" in q["refund"]["reason"]
    assert m.post(f"/api/v1/sync/quarantine/{q['refund']['id']}/retry").status_code == 409   # still blocked
    a.open_drawer(0)
    assert m.post(f"/api/v1/sync/quarantine/{q['sale']['id']}/retry").status_code == 200
    r = m.post(f"/api/v1/sync/quarantine/{q['refund']['id']}/retry")
    assert r.status_code == 200 and r.json()["status"] == "resolved", r.text
    assert m.post(f"/api/v1/sync/quarantine/{q['refund']['id']}/retry").status_code == 409   # already resolved
    [csale] = central_sales(app)
    [refund] = central_refunds(app)
    assert refund.original_sale_id == csale.id and refund.total_cents == 250
    assert m.get("/api/v1/sync/quarantine").json() == []


def test_refund_whose_amount_differs_is_quarantined_not_applied(edge, central, app, clock):
    edge.open_drawer(10_000)
    sale = edge.sell([("KOHV", 2)], 1000).json()
    assert edge.as_role("manager").refund(sale["id"], [(1, 1)]).status_code == 201
    with edge.app.state.session_factory() as s:                              # simulate a till that computed
        row = s.scalars(select(SyncOutbox).where(SyncOutbox.entry_type == "refund")).one()   # a different amount
        body = json.loads(row.payload)
        body["expect"]["total_cents"] = 999
        row.payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
        import hashlib
        row.payload_hash = hashlib.sha256(row.payload.encode()).hexdigest()
        s.commit()
    agent(edge, Net(central.c), clock).run_until_idle()
    [q] = central.as_role("manager").get("/api/v1/sync/quarantine").json()
    assert q["entry_type"] == "refund" and q["reason"].startswith("price_mismatch")
    assert central_refunds(app) == [] and len(central_sales(app)) == 1


def test_inbox_links_each_entry_to_the_document_it_created(edge, central, app, clock):
    edge.open_drawer(10_000)
    sale = edge.sell([("KOHV", 2)], 1000).json()
    assert edge.as_role("manager").refund(sale["id"], [(1, 2)]).status_code == 201
    agent(edge, Net(central.c), clock).run_until_idle()
    with app.state.session_factory() as s:
        rows = {r.entry_type: r for r in s.scalars(select(DeviceInbox))}
        assert s.get(Sale, rows["sale"].sale_id) is not None
        assert s.get(Refund, rows["refund"].sale_id).original_sale_id == rows["sale"].sale_id


def test_retry_never_refunds_twice_even_if_the_refund_already_exists(edge, central, app, clock):
    """Defence in depth behind the inbox: the refund's idempotency key is its
    journal entry id, so a retry of an entry whose refund already exists links
    to that refund instead of paying out again."""
    edge.open_drawer(10_000)
    sale = edge.sell([("KOHV", 2)], 1000).json()
    assert edge.as_role("manager").refund(sale["id"], [(1, 1)]).status_code == 201
    agent(edge, Net(central.c), clock).run_until_idle()
    [refund] = central_refunds(app)
    with app.state.session_factory.begin() as s:
        row = s.scalars(select(DeviceInbox).where(DeviceInbox.entry_type == "refund")).one()
        row.status, row.sale_id = "quarantined", None                       # inconsistent on purpose
    m = central.as_role("manager")
    [q] = m.get("/api/v1/sync/quarantine").json()
    clock.at += __import__("datetime").timedelta(minutes=5)                 # a manager retries later
    r = m.post(f"/api/v1/sync/quarantine/{q['id']}/retry")
    assert r.status_code == 200 and r.json()["status"] == "resolved", r.text
    assert [x.id for x in central_refunds(app)] == [refund.id]
