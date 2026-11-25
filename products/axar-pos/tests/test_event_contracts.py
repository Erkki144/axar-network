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


@pytest.fixture
def events(a, app):
    a.open_drawer(100_000)
    sales = []
    for lines in ([("KOHV", 1)], [("KOHV", 3), ("SAI", 2)], [("RAAMAT", 1), ("KOHV", 7)], [("SAI", 1)]):
        r = a.sell(lines, 20_000)
        assert r.status_code == 201, r.text
        sales.append(r.json())
    m = a.as_role("manager")
    for sale, lines in ((sales[1], [(1, 1)]), (sales[1], [(1, 2), (2, 2)]), (sales[2], [(1, 1)])):
        assert m.refund(sale["id"], lines).status_code == 201
    with app.state.session_factory() as s:
        return list(s.scalars(select(OutboxEvent).order_by(OutboxEvent.occurred_at)))


def test_every_outbox_event_matches_its_committed_schema(events):
    v = {"pos.sale.completed": validator("pos.sale.completed.v1.schema.json"),
         "pos.refund.completed": validator("pos.refund.completed.v1.schema.json")}
    assert {e.type for e in events} == set(v)
    for e in events:
        assert (e.version, e.source) == (1, "axar-pos")
        payload = json.loads(e.payload)
        errors = [err.message for err in v[e.type].iter_errors(payload)]
        assert errors == [], (e.type, errors)


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
