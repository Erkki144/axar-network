import pytest

from axar_pos.money import format_eur, round_cash, vat_from_gross


@pytest.mark.parametrize(
    "gross,rate,vat",
    [
        (124, 2400, 24),      # 1.24 incl 24 % -> 0.24 VAT
        (250, 2400, 48),      # 250*24/124 = 48.387 -> 48
        (1599, 900, 132),     # 1599*9/109 = 132.03 -> 132
        (100, 0, 0),
        (0, 2400, 0),
        (62, 2400, 12),       # 12.0 exactly
        (31, 2400, 6),        # 6.0
        (-250, 2400, -48),    # symmetric for refunds
    ],
)
def test_vat_from_gross(gross, rate, vat):
    assert vat_from_gross(gross, rate) == vat


def test_vat_half_up_boundary():
    # 1240 * 2400 / 12400 = 240 exactly; 13 cents @ 2400 -> 2.516 -> 3
    assert vat_from_gross(13, 2400) == 3


@pytest.mark.parametrize(
    "amount,rounded",
    [(100, 100), (101, 100), (102, 100), (103, 105), (104, 105), (107, 105), (108, 110), (0, 0)],
)
def test_cash_rounding_to_5_cents(amount, rounded):
    assert round_cash(amount, 5) == rounded


def test_cash_rounding_disabled():
    assert round_cash(103, 1) == 103


def test_format_eur():
    assert format_eur(1234) == "12,34 €"
    assert format_eur(5) == "0,05 €"
    assert format_eur(-250) == "-2,50 €"
