from dataclasses import replace
import pytest
from axar_connect import SaleLine, preview_cash_journal
from axar_connect.journal import Posting, Journal, canonical_document


def sums(j):
    return (sum(p.debit_cents for p in j.postings),sum(p.credit_cents for p in j.postings))


def test_normal_cash_sale_balance(sale,map_accounts):
    journal=preview_cash_journal(sale,**map_accounts)
    assert sums(journal)==(700,700)
    assert [(p.account,p.debit_cents,p.credit_cents) for p in journal.postings]==[
        ("CASH",700,0),("SALES",0,563),("VAT24",0,136),("GAIN",0,1)]


def test_deterministic_replay_same_event(sale,map_accounts):
    one=preview_cash_journal(sale,**map_accounts)
    two=preview_cash_journal(sale,**map_accounts)
    assert one.journal_id==two.journal_id
    assert canonical_document(one)==canonical_document(two)
    assert preview_cash_journal(replace(sale,event_id="e2"),**map_accounts).journal_id!=one.journal_id


def test_negative_rounding_is_debit(sale,map_accounts):
    test_snapshot=replace(sale,rounding_cents=-2,cash_due_cents=697)
    journal=preview_cash_journal(test_snapshot,**map_accounts)
    assert sums(journal)==(699,699)
    assert any(p.account=="LOSS" and p.debit_cents==2 for p in journal.postings)


def test_mixed_rate_including_13_and_zero(sale,map_accounts):
    s=replace(sale,lines=(SaleLine(250,202,48,2400),SaleLine(130,115,15,1300),SaleLine(199,199,0,0)),
              gross_cents=579,net_cents=516,vat_cents=63,rounding_cents=1,cash_due_cents=580)
    journal=preview_cash_journal(s,**map_accounts)
    assert sums(journal)==(580,580)
    assert [p.account for p in journal.postings]==["CASH","SALES","VAT13","VAT24","GAIN"]


def test_unknown_vat_mapping_fails_closed(sale,map_accounts):
    map_accounts["vat_accounts"]={900:"VAT9"}
    with pytest.raises(ValueError,match="missing VAT"):
        preview_cash_journal(sale,**map_accounts)


def test_posting_and_journal_refuse_invalid_balance():
    with pytest.raises(ValueError):
        Posting("cash",1,1)
    with pytest.raises(ValueError):
        Posting("cash",0,0)
    with pytest.raises(ValueError):
        Journal("t","e","s","j",(Posting("cash",10,0),))


@pytest.mark.parametrize("change",[
    {"gross_cents":700},{"net_cents":564},{"vat_cents":137},
    {"cash_due_cents":699},{"rounding_cents":5},{"currency":"USD"},
    {"tenant_id":""},{"register_id":""},{"receipt_no":0},
    {"lines":()},
])
def test_tampered_sale_rejected(sale,change):
    with pytest.raises(ValueError):
        replace(sale,**change)


@pytest.mark.parametrize("bad",["250",250.0,True,None])
def test_non_integer_money_rejected(bad):
    with pytest.raises(ValueError):
        SaleLine(bad,0,0,2400)


def test_negative_sale_cannot_hide_refund():
    with pytest.raises(ValueError):
        SaleLine(-100,-80,-20,2400)


def test_line_rate_must_match_computed_vat():
    with pytest.raises(ValueError,match="VAT does not match"):
        SaleLine(100,99,1,0)  # 0% VAT cannot contain one cent VAT
