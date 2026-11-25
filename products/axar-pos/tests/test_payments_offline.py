"""C3 bill payments on an offline till (edge node, O1b/O1c).

A table bill settled on the till is journaled AND queued for sync like a cash
sale; the central server does not re-create split/card/transfer bills
automatically yet, so the entry is acknowledged as quarantined for a manager
(never lost). The offline limit stops new bill payments too."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from axar_pos import offline
from axar_pos.api import create_app
from axar_pos.config import Settings
from axar_pos.models import DiningTable, Location, Product, Register, SyncOutbox, Tenant

from conftest import Api
from test_offline_sync import DEVICE, Net, agent, central, central_sales  # noqa: F401
from test_payments import bill, open_bill, pay

CARD_KEY = "5b0f7f0e-6a39-4c43-9d0a-0f3c1d2e4a11"


@pytest.fixture
def edge(tmp_path, clock):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'edge.db'}", demo_identity=True,
                              clock=clock, edge_device_id=DEVICE))
    with app.state.session_factory.begin() as s:
        s.add(Tenant(id="t-a", name="Kohvik A"))
        s.flush()
        s.add_all([Product(tenant_id="t-a", sku="KOHV", name="Kohv", price_cents=250)])
        loc = Location(tenant_id="t-a", code="L1", name="Kohvik A kesklinn")
        s.add(loc)
        s.flush()
        s.add_all([Register(tenant_id="t-a", location_id=loc.id, code="R1", name="Kassa 1"),
                   DiningTable(tenant_id="t-a", location_id=loc.id, code="A1", name="Laud A1")])
    with app.state.session_factory() as s:
        offline.record_contact(s, tenant_id="t-a", device_id=DEVICE, now=clock(), limit_hours=72)
    with TestClient(app) as c:
        api = Api(c, "t-a")
        api.app = app
        yield api
    app.state.engine.dispose()


def test_bill_paid_offline_is_queued_and_reaches_the_review_queue(edge, central, app, clock):
    edge.open_drawer(0)
    o = open_bill(edge, [("KOHV", 2)])
    assert pay(edge, o["id"], "card", 200, 500).status_code == 201
    r = pay(edge, o["id"], "cash", 300, 500, tendered_cents=300)
    assert r.status_code == 201, r.text
    assert bill(edge, o["id"])["status"] == "paid"
    with edge.app.state.session_factory() as s:
        [row] = s.scalars(select(SyncOutbox)).all()                        # one entry: the bill's sale
        body = json.loads(row.payload)
        assert body["kind"] == "bill" and body["lines"] == [["KOHV", 2]]
        assert sorted(body["tenders"]) == [["card", 200], ["cash", 300]]
    agent(edge, Net(central.c), clock).run_until_idle()
    with edge.app.state.session_factory() as s:
        assert s.scalars(select(SyncOutbox)).one().status == "quarantined"   # acknowledged, not lost
    [q] = central.as_role("manager").get("/api/v1/sync/quarantine").json()
    assert "table bill paid offline" in q["reason"] and q["payload"]["order_id"] == o["id"]
    assert central_sales(app) == []                                         # not re-created automatically


def test_offline_limit_stops_bill_payments_but_replays_still_answer(edge, clock):
    edge.open_drawer(0)
    o = open_bill(edge, [("KOHV", 2)])
    first = pay(edge, o["id"], "card", 200, 500, key=CARD_KEY)
    assert first.status_code == 201
    clock.at += timedelta(hours=73)
    r = pay(edge, o["id"], "cash", 300, 500, tendered_cents=300)
    assert r.status_code == 423 and r.json()["error"] == "offline_limit_exceeded"
    again = pay(edge, o["id"], "card", 200, 500, key=CARD_KEY)              # retry of a recorded payment
    assert again.status_code in (200, 201) and again.json()["payment"]["id"] == first.json()["payment"]["id"]
    assert bill(edge, o["id"])["status"] != "paid"
