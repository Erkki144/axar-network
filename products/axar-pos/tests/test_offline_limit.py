"""Offline limit O1c (owner decision 2026-11-25: a till may sell offline for
at most 72 h; the limit is set in the POS settings). The edge fixtures come
from test_offline_sync: the till was online once at enrolment (clock())."""

import json
from datetime import timedelta

from sqlalchemy import func, select

from axar_pos import offline
from axar_pos.models import AuthEvent, EdgeSyncState

from test_offline_sync import CENTRAL_H, DEVICE, Net, agent, central, central_sales, edge, outbox  # noqa: F401

H = timedelta(hours=1)


def sell(edge, key=None):
    return edge.sell([("KOHV", 1)], 1000, key=key)


def test_sales_stop_after_72h_without_contact_and_restart_when_the_connection_returns(edge, central, app, clock):
    edge.open_drawer(10_000)
    first = sell(edge, key="sale-before-limit")
    assert first.status_code == 201
    clock.at += 71 * H
    assert sell(edge).status_code == 201                                  # within the window
    clock.at += 2 * H                                                     # 73 h since the last contact
    r = sell(edge)
    assert r.status_code == 423 and r.json()["error"] == "offline_limit_exceeded"
    assert "72 h" in r.json()["message"] and "kept" in r.json()["message"]
    m = edge.as_role("manager")
    assert m.refund(first.json()["id"], [(1, 1)]).status_code == 423      # refunds are money too
    replay = sell(edge, key="sale-before-limit")                          # a retry of a sale already made
    assert replay.status_code in (200, 201) and replay.json()["id"] == first.json()["id"]
    assert len(outbox(edge, "pending")) == 2                              # nothing already sold is lost
    st = edge.get("/api/v1/sync/status").json()
    assert st["blocked"] and st["reason"] == "offline_limit_exceeded" and st["remaining_s"] == 0

    rounds = agent(edge, Net(central.c), clock).run_until_idle()          # the connection is back
    assert rounds[-1].state == "idle" and len(central_sales(app)) == 2
    assert sell(edge).status_code == 201                                  # contact lifted the block
    assert not edge.get("/api/v1/sync/status").json()["blocked"]


def test_a_heartbeat_renews_the_window_when_nothing_is_pending(edge, central, app, clock):
    edge.open_drawer(0)
    ag = agent(edge, Net(central.c), clock)
    clock.at += 70 * H
    r = ag.run_once()
    assert r.state == "idle" and r.detail == "heartbeat"
    clock.at += 70 * H                                                    # 140 h since enrolment, 70 h since contact
    assert sell(edge).status_code == 201
    assert ag.run_once().state == "sent"
    assert ag.run_once().detail == ""                                     # just in contact: no extra heartbeat


def test_failed_contact_does_not_extend_the_window(edge, central, app, clock):
    edge.open_drawer(0)
    net = Net(central.c)
    ag = agent(edge, net, clock)
    for mode in ("down", "server_error"):
        net.mode = mode
        clock.at += 36 * H
        assert ag.run_once().state == "offline"                           # heartbeat failed
    clock.at += 1 * H                                                     # 73 h since enrolment
    assert sell(edge).status_code == 423


def test_manager_sets_the_limit_and_the_till_gets_it_on_the_next_contact(edge, central, app, clock):
    m = central.as_role("manager")
    assert m.get("/api/v1/admin/pos-settings").json() == {"offline_limit_hours": 72, "offline_limit_max_hours": 72}
    for bad in (0, 73, 500):
        assert m.put("/api/v1/admin/pos-settings", {"offline_limit_hours": bad}).status_code == 422
    r = m.put("/api/v1/admin/pos-settings", {"offline_limit_hours": 12})
    assert r.status_code == 200 and r.json()["offline_limit_hours"] == 12
    with app.state.session_factory() as s:
        ev = s.scalars(select(AuthEvent).where(AuthEvent.event == "pos_settings.changed")).one()
        assert json.loads(ev.detail) == {"offline_limit_hours": {"before": 72, "after": 12}}
    edge.open_drawer(0)
    agent(edge, Net(central.c), clock, heartbeat_every=timedelta(0)).run_once()   # contact -> new policy
    assert edge.get("/api/v1/sync/status").json()["limit_hours"] == 12
    clock.at += 11 * H
    assert sell(edge).status_code == 201
    clock.at += 2 * H
    assert sell(edge).status_code == 423


def test_a_server_cannot_raise_the_limit_above_72h(edge, clock):
    with edge.app.state.session_factory() as s:
        offline.record_contact(s, tenant_id="t-a", device_id=DEVICE, now=clock(), limit_hours=500)
        assert s.get(EdgeSyncState, ("t-a", DEVICE)).offline_limit_hours == 72


def test_setting_the_clock_back_blocks_selling(edge, clock):
    edge.open_drawer(0)
    clock.at += 10 * H
    assert sell(edge).status_code == 201                                  # the till has now seen +10 h
    clock.at -= 9 * H                                                     # someone sets the clock back
    r = sell(edge)
    assert r.status_code == 423 and r.json()["error"] == "clock_moved_back"
    clock.at += 9 * H                                                     # clock fixed
    assert sell(edge).status_code == 201


def test_a_till_that_never_connected_cannot_sell(edge, clock):
    with edge.app.state.session_factory() as s:
        s.delete(s.get(EdgeSyncState, ("t-a", DEVICE)))
        s.commit()
    edge.open_drawer(0)
    r = sell(edge)
    assert r.status_code == 423 and "not connected" in r.json()["message"]
    assert edge.get("/api/v1/sync/status").json()["reason"] == "never_connected"


def test_the_central_server_is_never_limited(central, app, clock):
    clock.at += 500 * H
    assert central.sell([("KOHV", 1)], 1000).status_code == 201
    assert central.get("/api/v1/sync/status").status_code == 404
    with app.state.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(EdgeSyncState)) == 0
