import hashlib
import json
from copy import deepcopy
import pytest
from axar_connect import from_pos_outbox_v1, preview_cash_journal


def envelope():
    payload={
        "sale_id":"sale-1","receipt_no":1,"register_id":3,
        "drawer_session_id":5,"business_date":"2026-11-22","payment_method":"cash",
        "total_cents":795,"vat_cents":154,"net_cents":641,
        "cash_due_cents":795,"cash_rounding_cents":0,
        "lines":[
            {"sku":"KOHV","quantity":2,"unit_price_cents":250,"gross_cents":500,"vat_rate_bp":2400,"vat_cents":97},
            {"sku":"SAIAKE","quantity":1,"unit_price_cents":295,"gross_cents":295,"vat_rate_bp":2400,"vat_cents":57},
        ],
    }
    raw=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(",", ":"))
    return {"id":"evt-1","tenant_id":"tenant-A","type":"pos.sale.completed",
            "version":1,"source":"axar-pos","payload":raw,
            "payload_hash":hashlib.sha256(raw.encode()).hexdigest()}


def rehash(ev,raw):
    ev["payload"]=raw
    ev["payload_hash"]=hashlib.sha256(raw.encode()).hexdigest()


def test_current_claude_pos_outbox_schema_to_balanced_journal(map_accounts):
    s=from_pos_outbox_v1(envelope())
    assert s.tenant_id=="tenant-A" and s.register_id=="3"
    assert s.gross_cents==795 and s.net_cents==641 and s.vat_cents==154
    j=preview_cash_journal(s,**map_accounts)
    assert sum(x.debit_cents for x in j.postings)==795
    assert sum(x.credit_cents for x in j.postings)==795


def test_reject_modified_payload_without_updated_checksum():
    ev=envelope()
    ev["payload"]=ev["payload"].replace('"total_cents":795','"total_cents":1')
    with pytest.raises(ValueError,match="checksum"):
        from_pos_outbox_v1(ev)


def test_reject_wrong_event_type_or_version():
    for field,value in (("type","refund.completed"),("version",2),("source","other")):
        ev=envelope();ev[field]=value
        with pytest.raises(ValueError,match="type/version/source"):
            from_pos_outbox_v1(ev)


def test_reject_forged_totals_with_matching_hash():
    ev=envelope();data=json.loads(ev["payload"]);data["total_cents"]=999
    rehash(ev,json.dumps(data))
    with pytest.raises(ValueError,match="totals"):
        from_pos_outbox_v1(ev)


def test_reject_duplicate_keys_even_with_updated_hash():
    ev=envelope();rehash(ev,'{"payment_method":"cash","payment_method":"card"}')
    with pytest.raises(ValueError,match="duplicate"):
        from_pos_outbox_v1(ev)


def test_reject_float_amount_even_with_valid_hash():
    ev=envelope();data=json.loads(ev["payload"]);data["vat_cents"]=154.0
    rehash(ev,json.dumps(data))
    with pytest.raises(ValueError,match="integer"):
        from_pos_outbox_v1(ev)


def test_reject_card_payment():
    ev=envelope();data=json.loads(ev["payload"]);data["payment_method"]="card"
    rehash(ev,json.dumps(data))
    with pytest.raises(ValueError,match="cash-sale"):
        from_pos_outbox_v1(ev)
