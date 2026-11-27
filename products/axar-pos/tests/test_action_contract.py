"""Fail-closed semantic button contract checks (issue #81)."""
import pytest

from axar_pos.action_contract import (
    ACTIONS, ActionButton, PolicyError, validate_button, validate_layout,
)


def test_product_button_binds_a_real_identifier():
    definition = validate_button(ActionButton("sale.add_product", "Kohv", 0, 42))
    assert definition.intent == "sale.add_product"
    assert definition.required_inputs == ("product_id",)


@pytest.mark.parametrize("product_id", [None, 0, -1, True, "42"])
def test_product_button_requires_positive_integer_id(product_id):
    with pytest.raises(PolicyError):
        validate_button(ActionButton("sale.add_product", "Kohv", 0, product_id))


def test_cash_out_cannot_downgrade_confirmation_or_offline_policy():
    d = validate_button(ActionButton("drawer.cash_out", "Sularaha välja", 1))
    assert d.confirmation == "always"
    assert d.offline == "online_only"
    assert d.irreversible
    assert d.required_inputs == ("amount_cents", "reason_code")


def test_office_cannot_publish_arbitrary_action():
    with pytest.raises(PolicyError):
        validate_button(ActionButton("admin.delete_all", "Kustuta kõik", 1))


def test_non_product_button_cannot_attach_product():
    with pytest.raises(PolicyError):
        validate_button(ActionButton("sale.pay_cash", "Sularaha", 0, 3))


def test_layout_has_deterministic_order():
    result = validate_layout([
        ActionButton("sale.pay_cash", "Sularaha", 2),
        ActionButton("sale.add_product", "Kohv", 1, 42),
    ])
    assert [x.position for x in result] == [1, 2]


def test_duplicate_position_is_rejected():
    with pytest.raises(PolicyError):
        validate_layout([
            ActionButton("sale.pay_cash", "Sularaha", 0),
            ActionButton("sale.pay_card", "Kaart", 0),
        ])


@pytest.mark.parametrize("label,position", [("", 1), ("   ", 1), ("x" * 61, 1), ("Ok", -1), ("Ok", 1000)])
def test_invalid_button_metadata(label, position):
    with pytest.raises(PolicyError):
        validate_button(ActionButton("sale.pay_cash", label, position))


def test_all_effectful_actions_have_auditable_policy():
    for intent, action in ACTIONS.items():
        assert action.intent == intent
        assert action.permission and action.audit_verb
        assert action.confirmation in ("never", "conditional", "always")
        assert action.offline in ("allow_queued", "online_only", "deny")
