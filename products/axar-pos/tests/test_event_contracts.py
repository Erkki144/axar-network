"""Producer-side contract tests: every event POS writes to its outbox must
validate against the committed schema in products/axar-one/architecture/
contracts/events and satisfy the invariants consumers (AXAR CONNECT, PR #8)
re-check. A new payload field fails here first, which forces a schema version
bump instead of silently breaking consumers."""

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select

from axar_pos.models import OutboxEvent

SCHEMAS = Path(__file__).resolve().parents[2] / "axar-one" / "architecture" / "contracts" / "events"


def validator(name):
    schema = json.loads((SCHEMAS / name).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def half_up_vat(gross, rate_bp):
    d = 10000 + rate_bp
    return (2 * gross * rate_bp + d) // (2 * d)


def table_id(api, code):
    return next(t["id"] for t in api.get("/api/v1/tables").json() if t["code"] == code)


@pytest.fixture
def events(a, app):
    d = a.open_drawer(100_000)
    sales = []
    for lines in ([("KOHV", 1)], [("KOHV", 3), ("SAI", 2)], [("RAAMAT", 1), ("KOHV", 7)], [("SAI", 1)]):
        r = a.sell(lines, 20_000)
        assert r.status_code == 201, r.text
        sales.append(r.json())
    m = a.as_role("manager")
    for sale, lines in ((sales[1], [(1, 1)]), (sales[1], [(1, 2), (2, 2)]), (sales[2], [(1, 1)])):
        assert m.refund(sale["id"], lines).status_code == 201
    # table service: open, edit, kitchen, ready, pay
    o = a.post(f"/api/v1/tables/{table_id(a, 'A1')}/order").json()
    o = a.c.put(f"/api/v1/orders/{o['id']}/lines", headers=a.h, json={"version": o["version"], "lines": [
        {"product_id": a.product_id("KOHV"), "quantity": 2}]}).json()
    k = a.post(f"/api/v1/orders/{o['id']}/kitchen", {"version": o["version"]},
               headers={"Idempotency-Key": "contract-kitchen-1"}).json()
    assert a.post(f"/api/v1/kitchen/tickets/{k['ticket']['id']}/ready").status_code == 200
    assert a.post(f"/api/v1/orders/{o['id']}/pay-cash", {"version": k["order"]["version"],
                  "register_id": a.register_id(), "tendered_cents": 1000},
                  headers={"Idempotency-Key": "contract-pay-0001"}).status_code == 201
    assert a.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 0}).status_code == 200
    with app.state.session_factory() as s:
        return list(s.scalars(select(OutboxEvent).order_by(OutboxEvent.recorded_seq)))


ALL_TYPES = {"pos.sale.completed", "pos.refund.completed", "drawer.opened", "drawer.closed", "order.opened",
             "order.changed", "order.closed", "kitchen.ticket.created", "kitchen.ticket.ready"}


def test_every_outbox_event_matches_its_committed_schema(events):
    assert {e.type for e in events} == ALL_TYPES
    cache = {}
    for e in events:
        assert e.source == "axar-pos"
        name = f"{e.type}.v{e.version}.schema.json"
        assert (SCHEMAS / name).exists(), f"no committed schema for {name}"
        v = cache.setdefault(name, validator(name))
        errors = [err.message for err in v.iter_errors(json.loads(e.payload))]
        assert errors == [], (e.type, errors)


def test_envelope_v2_fields_and_gap_free_sequence(events):
    seqs = [e.recorded_seq for e in events]
    assert seqs == list(range(1, len(events) + 1))            # per tenant, gap-free, commit order
    for e in events:
        assert e.aggregate_type and e.aggregate_id and e.aggregate_version >= 1
        assert json.loads(e.actor)["kind"] in {"staff", "system"}
    by_order = [e.aggregate_version for e in events if e.aggregate_type == "order"]
    assert by_order == sorted(by_order) and len(set(by_order)) == len(by_order)   # per-aggregate order
    closed = next(e for e in events if e.type == "order.closed")
    sale = next(e for e in events if e.type == "pos.sale.completed"
                and json.loads(e.payload)["sale_id"] == json.loads(closed.payload)["sale_id"])
    assert sale.recorded_seq < closed.recorded_seq            # same transaction, sale first


def test_payload_is_canonical_and_hash_matches(events):
    for e in events:
        canon = json.dumps(json.loads(e.payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert e.payload == canon
        assert e.payload_hash == hashlib.sha256(e.payload.encode()).hexdigest()


def test_sale_financial_identities_consumers_rely_on(events):
    for e in (x for x in events if x.type == "pos.sale.completed"):
        p = json.loads(e.payload)
        assert p["total_cents"] == sum(ln["gross_cents"] for ln in p["lines"])
        assert p["vat_cents"] == sum(ln["vat_cents"] for ln in p["lines"])
        assert p["net_cents"] == p["total_cents"] - p["vat_cents"]
        assert p["cash_due_cents"] == p["total_cents"] + p["cash_rounding_cents"]
        assert p["cash_due_cents"] % 5 == 0
        for ln in p["lines"]:
            assert ln["gross_cents"] == ln["unit_price_cents"] * ln["quantity"]
            assert ln["vat_cents"] == half_up_vat(ln["gross_cents"], ln["vat_rate_bp"])  # CONNECT re-check


def test_refund_financial_identities(events):
    for e in (x for x in events if x.type == "pos.refund.completed"):
        p = json.loads(e.payload)
        assert p["total_cents"] == sum(ln["gross_cents"] for ln in p["lines"])
        assert p["vat_cents"] == sum(ln["vat_cents"] for ln in p["lines"])
        assert p["net_cents"] == p["total_cents"] - p["vat_cents"]
        assert p["cash_paid_cents"] == p["total_cents"] + p["cash_rounding_cents"]


def test_schema_rejects_unknown_fields_so_changes_need_a_new_version():
    v = validator("pos.sale.completed.v1.schema.json")
    sample = {"sale_id": "00000000-0000-0000-0000-000000000001", "receipt_no": 1, "register_id": 1,
              "drawer_session_id": 1, "business_date": "2026-03-10", "payment_method": "cash", "total_cents": 250,
              "vat_cents": 48, "net_cents": 202, "cash_due_cents": 250, "cash_rounding_cents": 0,
              "lines": [{"sku": "KOHV", "quantity": 1, "unit_price_cents": 250, "gross_cents": 250,
                         "vat_rate_bp": 2400, "vat_cents": 48}]}
    assert list(v.iter_errors(sample)) == []
    assert list(v.iter_errors({**sample, "currency": "EUR"}))


def test_stock_changed_events_match_schema(a, app):
    from axar_pos import inventory
    from axar_pos.db import bind_tenant

    with app.state.request_factory() as s:
        bind_tenant(s, "t-a")
        loc = next(r for r in a.get("/api/v1/registers").json())["location"]["id"]
        wh = inventory.create_warehouse(s, "t-a", location_id=loc, code="W", name="W", is_default=True)
        it = inventory.create_item(s, "t-a", sku="I", name="I", kind="ingredient", uom="kg")
        sp = inventory.create_supplier(s, "t-a", code="S", name="S")
        from datetime import date, datetime, timezone
        now = datetime(2026, 3, 10, 10, tzinfo=timezone.utc)
        d = inventory.create_document(s, "t-a", supplier_id=sp.id, doc_type="invoice", doc_no="1", doc_date=date(2026, 3, 10),
                                      warehouse_id=wh.id, lines=[inventory.DocLine(it.id, 1_000_000, 500, 2400)],
                                      freight_cents=0, actor="k", now=now)
        inventory.post_document(s, "t-a", d.id, actor="k", now=now)
    v = validator("stock.changed.v1.schema.json")
    with app.state.session_factory() as s:
        evs = list(s.scalars(select(OutboxEvent).where(OutboxEvent.type == "stock.changed")))
    assert len(evs) == 1 and list(v.iter_errors(json.loads(evs[0].payload))) == []
