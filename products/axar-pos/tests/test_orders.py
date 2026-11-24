"""Slice 4: server-side table orders, kitchen tickets, order payment, operation status."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from axar_pos.models import KitchenTicket, Order, Sale


def table_id(api, code):
    return next(t["id"] for t in api.get("/api/v1/tables").json() if t["code"] == code)


def open_order(api, code="A1"):
    r = api.post(f"/api/v1/tables/{table_id(api, code)}/order")
    assert r.status_code == 200, r.text
    return r.json()


def set_lines(api, order, lines):
    return api.c.put(f"/api/v1/orders/{order['id']}/lines", headers=api.h,
                     json={"version": order["version"],
                           "lines": [{"product_id": api.product_id(sku), "quantity": q} for sku, q in lines]})


def send(api, order, key=None):
    return api.post(f"/api/v1/orders/{order['id']}/kitchen", {"version": order["version"]},
                    headers={"Idempotency-Key": key or str(uuid.uuid4())})


def pay(api, order, tendered, key=None, register="R1"):
    return api.post(f"/api/v1/orders/{order['id']}/pay-cash",
                    {"version": order["version"], "register_id": api.register_id(register), "tendered_cents": tendered},
                    headers={"Idempotency-Key": key or str(uuid.uuid4())})


def count(app, model):
    with app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(model))


# ------------------------------------------------------------------ orders


def test_open_order_is_get_or_create(a):
    o1, o2 = open_order(a), open_order(a)
    assert o1["id"] == o2["id"] and o1["status"] == "open" and o1["version"] == 1
    tables = {t["code"]: t for t in a.get("/api/v1/tables").json()}
    assert tables["A1"]["open_order"]["id"] == o1["id"] and tables["A2"]["open_order"] is None


def test_lines_are_versioned_and_stale_writes_rejected(a):
    o = open_order(a)
    r = set_lines(a, o, [("KOHV", 2)])
    assert r.status_code == 200 and r.json()["version"] == 2
    stale = set_lines(a, o, [("SAI", 1)])           # still version 1: another device saw old state
    assert stale.status_code == 409 and stale.json()["error"] == "order_changed"
    now = a.get(f"/api/v1/orders/{o['id']}").json()
    assert [(x["quantity"]) for x in now["lines"]] == [2]


def test_duplicate_product_lines_rejected(a):
    o = open_order(a)
    r = set_lines(a, o, [("KOHV", 1), ("KOHV", 2)])
    assert r.status_code == 422


# ------------------------------------------------------------------ kitchen


def test_send_to_kitchen_sends_only_unsent_and_is_idempotent(a, app):
    o = set_lines(a, open_order(a), [("KOHV", 2), ("SAI", 1)]).json()
    r1 = send(a, o, key="kitchen-key-0001")
    assert r1.status_code == 201
    t1 = r1.json()["ticket"]
    assert sorted((ln["name"], ln["quantity"]) for ln in t1["lines"]) == [("Kohv", 2), ("Saiake", 1)]
    again = send(a, o, key="kitchen-key-0001")        # retry after lost response
    assert again.status_code == 200 and again.json()["ticket"]["id"] == t1["id"]
    o = r1.json()["order"]
    assert send(a, o).json()["error"] == "nothing_to_send"
    o = set_lines(a, o, [("KOHV", 3), ("SAI", 1)]).json()
    t2 = send(a, o).json()["ticket"]
    assert [(ln["name"], ln["quantity"]) for ln in t2["lines"]] == [("Kohv", 1)]   # only the delta
    assert count(app, KitchenTicket) == 2


def test_cannot_reduce_below_sent_quantity(a):
    o = set_lines(a, open_order(a), [("KOHV", 2)]).json()
    o = send(a, o).json()["order"]
    r = set_lines(a, o, [("KOHV", 1)])
    assert r.status_code == 422 and r.json()["error"] == "invalid_order"
    assert a.get(f"/api/v1/orders/{o['id']}").json()["lines"][0]["quantity"] == 2


def test_ticket_ready_is_idempotent_and_tenant_scoped(a, b):
    o = set_lines(a, open_order(a), [("KOHV", 1)]).json()
    t = send(a, o).json()["ticket"]
    assert a.post(f"/api/v1/kitchen/tickets/{t['id']}/ready").json()["status"] == "ready"
    assert a.post(f"/api/v1/kitchen/tickets/{t['id']}/ready").json()["status"] == "ready"
    assert b.post(f"/api/v1/kitchen/tickets/{t['id']}/ready").status_code == 404
    assert b.get("/api/v1/kitchen/tickets").json() == []


# ------------------------------------------------------------------ payment


def test_pay_order_creates_one_sale_and_closes_order(a, app):
    a.open_drawer()
    o = set_lines(a, open_order(a), [("KOHV", 2), ("SAI", 1)]).json()
    r = pay(a, o, 1000, key="order-pay-0001")
    assert r.status_code == 201, r.text
    sale = r.json()
    assert (sale["total_cents"], sale["cash_due_cents"], sale["change_cents"]) == (699, 700, 300)
    closed = a.get(f"/api/v1/orders/{o['id']}").json()
    assert closed["status"] == "paid" and closed["sale_id"] == sale["id"]
    replay = pay(a, o, 1000, key="order-pay-0001")    # lost response -> same key
    assert replay.status_code == 200 and replay.json()["id"] == sale["id"]
    second = pay(a, o, 1000)                          # another device, new key
    assert second.status_code == 409
    assert count(app, Sale) == 1
    assert set_lines(a, closed, [("KOHV", 1)]).json()["error"] == "order_not_open"
    assert open_order(a)["id"] != o["id"]             # table free for a new bill


def test_pay_with_stale_version_is_rejected_and_order_stays_open(a, app):
    a.open_drawer()
    o = set_lines(a, open_order(a), [("KOHV", 1)]).json()
    newer = set_lines(a, o, [("KOHV", 2)]).json()     # another device added an item
    r = pay(a, o, 1000)                               # pays the bill it saw (v2) not v3
    assert r.status_code == 409 and r.json()["error"] == "order_changed"
    assert count(app, Sale) == 0
    assert a.get(f"/api/v1/orders/{o['id']}").json()["status"] == "open"
    assert pay(a, newer, 1000).status_code == 201


def test_pay_without_open_drawer_rolls_back(a, app):
    o = set_lines(a, open_order(a), [("KOHV", 1)]).json()
    r = pay(a, o, 250)
    assert r.status_code == 409 and r.json()["error"] == "no_open_drawer"
    assert a.get(f"/api/v1/orders/{o['id']}").json()["status"] == "open"
    assert count(app, Sale) == 0


def test_empty_order_cannot_be_paid(a):
    a.open_drawer()
    r = pay(a, open_order(a), 1000)
    assert r.status_code == 422


def test_parallel_payment_of_same_order_from_many_devices(a, app):
    a.open_drawer()
    o = set_lines(a, open_order(a), [("KOHV", 2)]).json()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: pay(a, o, 500, key=f"device-{i}-pay"), range(8)))
    assert sum(r.status_code == 201 for r in results) == 1
    assert all(r.status_code == 409 for r in results if r.status_code != 201)
    assert count(app, Sale) == 1
    with app.state.session_factory() as s:
        assert s.get(Order, o["id"]).status == "paid"
    assert a.drawer()["sale_count"] == 1


# ------------------------------------------------------------------ tenancy


def test_orders_are_tenant_scoped(a, b):
    o = open_order(a)
    assert b.get(f"/api/v1/orders/{o['id']}").status_code == 404
    assert b.post(f"/api/v1/tables/{table_id(a, 'A1')}/order").status_code == 404
    r = b.c.put(f"/api/v1/orders/{o['id']}/lines", headers=b.h, json={"version": 1, "lines": []})
    assert r.status_code == 404
    assert {t["code"] for t in b.get("/api/v1/tables").json()} == {"B1"}


# --------------------------------------------------------- operation status


def test_operation_status_by_key(a, b):
    a.open_drawer()
    assert a.get("/api/v1/operations/never-used-key-1").json() == {"status": "not_recorded"}
    sale = a.sell([("KOHV", 1)], 250, key="status-key-0001").json()
    st = a.get("/api/v1/operations/status-key-0001").json()
    assert st == {"status": "recorded", "kind": "sale", "id": sale["id"]}
    m = a.as_role("manager")
    rf = m.refund(sale["id"], [(1, 1)], key="status-key-0002").json()
    assert a.get("/api/v1/operations/status-key-0002").json()["id"] == rf["id"]
    assert b.get("/api/v1/operations/status-key-0001").json() == {"status": "not_recorded"}
