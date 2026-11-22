"""Regression tests against Claude's actual pos.refund.completed v1 payload."""
from hashlib import sha256
import json
import pytest

from axar_connect import from_pos_refund_outbox_v1, preview_cash_refund_journal
from axar_connect.refunds import CashRefund, CashRefundLine


def refund_envelope():
    payload = {
        "refund_id": "refund-1", "refund_no": 1,
        "original_sale_id": "sale-1", "original_receipt_no": 9,
        "original_correlation_id": "corr-1", "register_id": 3,
        "drawer_session_id": 5, "business_date": "2026-11-22",
        "payment_method": "cash", "simulated": True,
        "total_cents": 699, "net_cents": 563, "vat_cents": 136,
        "cash_paid_cents": 700, "cash_rounding_cents": 1,
        "approver_role": "manager",
        "lines": [
            {"sku": "KOHV", "quantity": 2, "gross_cents": 500,
             "vat_rate_bp": 2400, "vat_cents": 97},
            {"sku": "SAI", "quantity": 1, "gross_cents": 199,
             "vat_rate_bp": 2400, "vat_cents": 39},
        ],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {"id": "evt-refund-1", "tenant_id": "t-a",
            "type": "pos.refund.completed", "version": 1,
            "source": "axar-pos", "payload": raw,
            "payload_hash": sha256(raw.encode()).hexdigest()}


def update(ev, mutate):
    data = json.loads(ev["payload"])
    mutate(data)
    raw = json.dumps(data, separators=(",", ":"))
    ev["payload"] = raw
    ev["payload_hash"] = sha256(raw.encode()).hexdigest()


def test_real_claude_refund_contract_to_balanced_preview(map_accounts):
    refund = from_pos_refund_outbox_v1(refund_envelope())
    assert (refund.original_sale_id, refund.register_id, refund.cash_paid_cents) == ("sale-1", "3", 700)
    j = preview_cash_refund_journal(refund, **map_accounts)
    assert sum(p.debit_cents for p in j.postings) == 700
    assert sum(p.credit_cents for p in j.postings) == 700
    assert [(p.account, p.debit_cents, p.credit_cents) for p in j.postings] == [
        ("SALES", 563, 0), ("VAT24", 136, 0), ("LOSS", 1, 0), ("CASH", 0, 700)
    ]


def test_same_event_has_stable_journal_id(map_accounts):
    x = from_pos_refund_outbox_v1(refund_envelope())
    assert preview_cash_refund_journal(x, **map_accounts).journal_id == preview_cash_refund_journal(x, **map_accounts).journal_id


def test_tampering_invalid_checksum():
    ev = refund_envelope()
    ev["payload"] = ev["payload"].replace('"total_cents":699', '"total_cents":111')
    with pytest.raises(ValueError, match="checksum"):
        from_pos_refund_outbox_v1(ev)


@pytest.mark.parametrize("field,value", [
    ("type", "pos.sale.completed"), ("version", 2), ("version", True),
    ("source", "fake"),
])
def test_reject_other_event_type_or_version(field, value):
    ev = refund_envelope(); ev[field] = value
    with pytest.raises(ValueError, match="type/version/source"):
        from_pos_refund_outbox_v1(ev)


def test_reject_float_amount_even_if_rehashed():
    ev = refund_envelope(); update(ev, lambda d: d.update(vat_cents=136.0))
    with pytest.raises(ValueError, match="integer"):
        from_pos_refund_outbox_v1(ev)


def test_reject_forged_total_even_if_rehashed():
    ev = refund_envelope(); update(ev, lambda d: d.update(total_cents=999))
    with pytest.raises(ValueError, match="totals"):
        from_pos_refund_outbox_v1(ev)


def test_reject_negative_net_from_pos_partial_refund_bug():
    ev = refund_envelope()
    def mutation(d):
        d["lines"] = [{"sku": "MICRO", "quantity": 1, "gross_cents": 1,
                       "vat_rate_bp": 2400, "vat_cents": 19}]
        d.update(total_cents=1, vat_cents=19, net_cents=-18,
                 cash_paid_cents=0, cash_rounding_cents=-1)
    update(ev, mutation)
    with pytest.raises(ValueError, match="VAT must be between zero and refunded gross"):
        from_pos_refund_outbox_v1(ev)


def test_reject_bad_cash_rounding_or_negative_cash():
    for key,value in (("cash_paid_cents", 699), ("cash_rounding_cents", 5),
                      ("cash_paid_cents", -5)):
        ev = refund_envelope();update(ev,lambda d: d.update({key:value}))
        with pytest.raises(ValueError):
            from_pos_refund_outbox_v1(ev)


def test_reject_non_simulated_refund_and_card():
    for key,value in (("simulated", False),("payment_method", "card")):
        ev = refund_envelope();update(ev,lambda d:d.update({key:value}))
        with pytest.raises(ValueError, match="simulated cash"):
            from_pos_refund_outbox_v1(ev)


def test_reject_missing_vat_account(map_accounts):
    refund = from_pos_refund_outbox_v1(refund_envelope())
    map_accounts["vat_accounts"] = {}
    with pytest.raises(ValueError, match="VAT reversal"):
        preview_cash_refund_journal(refund, **map_accounts)


def test_zero_tax_refund_still_balances(map_accounts):
    refund = CashRefund("t", "ev", "refund", "sale", "1", __import__("datetime").date(2026,11,22), 1,
        (CashRefundLine("X", 1, 5, 0, 0),), 5, 5, 0, 0, 5)
    j = preview_cash_refund_journal(refund, **map_accounts)
    assert sum(p.debit_cents for p in j.postings) == sum(p.credit_cents for p in j.postings) == 5


def test_reject_duplicate_json_keys():
    ev=refund_envelope()
    raw='{"refund_id":"1","refund_id":"2"}'
    ev["payload"]=raw;ev["payload_hash"]=sha256(raw.encode()).hexdigest()
    with pytest.raises(ValueError,match="duplicate"):
        from_pos_refund_outbox_v1(ev)


def test_negative_rounding_refund_books_gain(map_accounts):
    ev = refund_envelope()
    def change(d):
        d["lines"] = [{"sku":"ROUND", "quantity":1,"gross_cents":696,
                       "vat_rate_bp":2400,"vat_cents":135}]
        d.update(total_cents=696,vat_cents=135,net_cents=561,
                 cash_rounding_cents=-1,cash_paid_cents=695)
    update(ev,change)
    journal=preview_cash_refund_journal(from_pos_refund_outbox_v1(ev),**map_accounts)
    assert sum(p.debit_cents for p in journal.postings)==696
    assert sum(p.credit_cents for p in journal.postings)==696
    assert any(p.account=="GAIN" and p.credit_cents==1 for p in journal.postings)
