from dataclasses import replace
import pytest
from axar_connect import Payout,BankEntry,reconcile_payouts
from axar_connect.reconcile import Status


def payout(t="t-a",provider="payco",ref="P-1"):
    return Payout(t,provider,ref,10000,300,200,9500)


def bank(t="t-a",ref="p-1",amount=9500,id="b-1"):
    return BankEntry(t,id,"bank-acct",ref,amount)


def test_match_exact_reference_and_net_amount():
    assert reconcile_payouts([payout()],[bank()])[0].status==Status.MATCHED


def test_equal_amount_wrong_reference_never_matches():
    assert reconcile_payouts([payout()],[bank(ref="unknown")])[0].status==Status.MISSING_BANK


def test_other_tenant_reference_not_accepted():
    assert reconcile_payouts([payout()],[bank(t="t-b")])[0].status==Status.MISSING_BANK


def test_one_cent_difference_flagged():
    m=reconcile_payouts([payout()],[bank(amount=9499)])[0]
    assert m.status==Status.AMOUNT_MISMATCH and m.actual_cents==9499


def test_duplicate_bank_reference_is_ambiguous():
    assert reconcile_payouts([payout()],[bank(id="b-1"),bank(id="b-2")])[0].status==Status.AMBIGUOUS


def test_same_reference_shared_by_providers_ambiguous_for_both():
    r=reconcile_payouts([payout(),payout(provider="other")],[bank()])
    assert len(r)==2 and all(m.status==Status.AMBIGUOUS for m in r)


def test_duplicate_payout_id_rejected():
    with pytest.raises(ValueError,match="duplicate provider payout"):
        reconcile_payouts([payout(),payout()],[])


def test_duplicate_bank_id_rejected():
    with pytest.raises(ValueError,match="duplicate bank"):
        reconcile_payouts([payout()],[bank(),bank()])


def test_identical_refs_separate_tenants():
    r=reconcile_payouts([payout(t="t-a"),payout(t="t-b")],
                        [bank(t="t-a",id="a"),bank(t="t-b",id="b")])
    assert all(x.status==Status.MATCHED for x in r)


def test_missing_bank():
    assert reconcile_payouts([payout()],[])[0].status==Status.MISSING_BANK


def test_payout_arithmetic_invalid():
    with pytest.raises(ValueError,match="payout net"):
        replace(payout(),bank_due_cents=9400)


def test_bank_float_rejected():
    with pytest.raises(ValueError):
        bank(amount=9500.0)
