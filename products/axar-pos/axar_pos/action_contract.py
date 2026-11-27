"""Immutable semantic-action policy contracts for AXAR ONE POS.

This module is deliberately data-only: a button cannot invent an API route,
override authorization, execute code, or change ledger semantics. Server
handlers must still enforce the authenticated session and current grants.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Intent = Literal[
    "sale.add_product", "sale.change_quantity", "sale.pay_cash",
    "sale.pay_card", "order.send_kitchen", "drawer.cash_in",
    "drawer.cash_out", "sale.refund",
]
Confirmation = Literal["never", "conditional", "always"]
Offline = Literal["allow_queued", "online_only", "deny"]


@dataclass(frozen=True)
class ActionDefinition:
    intent: Intent
    permission: str
    required_inputs: tuple[str, ...]
    confirmation: Confirmation
    offline: Offline
    audit_verb: str
    irreversible: bool = False


# Code-owned behavior, not edited by the office UI.
ACTIONS: dict[str, ActionDefinition] = {
    "sale.add_product": ActionDefinition("sale.add_product", "sale", ("product_id",), "never", "allow_queued", "sale.line_add"),
    "sale.change_quantity": ActionDefinition("sale.change_quantity", "sale", ("line_id", "quantity"), "never", "allow_queued", "sale.quantity_change"),
    "sale.pay_cash": ActionDefinition("sale.pay_cash", "sale", (), "conditional", "online_only", "payment.cash", True),
    "sale.pay_card": ActionDefinition("sale.pay_card", "sale", (), "conditional", "online_only", "payment.card", True),
    "order.send_kitchen": ActionDefinition("order.send_kitchen", "orders", (), "conditional", "online_only", "order.kitchen_send", True),
    "drawer.cash_in": ActionDefinition("drawer.cash_in", "drawer", ("amount_cents", "reason_code"), "conditional", "online_only", "drawer.cash_in", True),
    "drawer.cash_out": ActionDefinition("drawer.cash_out", "drawer", ("amount_cents", "reason_code"), "always", "online_only", "drawer.cash_out", True),
    "sale.refund": ActionDefinition("sale.refund", "refund.request", ("sale_id", "reason"), "always", "online_only", "sale.refund", True),
}


@dataclass(frozen=True)
class ActionButton:
    intent: str
    label: str
    position: int
    product_id: int | None = None


class PolicyError(ValueError):
    pass


def validate_button(button: ActionButton) -> ActionDefinition:
    """Validate an OFFICE-authored presentation setting before publication.

    Only allowlisted intents are valid. The office may change presentation,
    not authorization, input requirements, money behavior, or confirmation.
    """
    definition = ACTIONS.get(button.intent)
    if definition is None:
        raise PolicyError("unknown intent")
    if not button.label.strip() or len(button.label) > 60:
        raise PolicyError("invalid label")
    if not 0 <= button.position <= 999:
        raise PolicyError("invalid button position")
    if button.intent == "sale.add_product":
        if button.product_id is None or type(button.product_id) is not int or button.product_id <= 0:
            raise PolicyError("product button requires immutable product ID")
    elif button.product_id is not None:
        raise PolicyError("only product actions may bind a product")
    return definition


def validate_layout(buttons: list[ActionButton]) -> tuple[ActionButton, ...]:
    if len(buttons) > 200:
        raise PolicyError("too many buttons")
    if len({b.position for b in buttons}) != len(buttons):
        raise PolicyError("duplicate button position")
    for button in buttons:
        validate_button(button)
    return tuple(sorted(buttons, key=lambda b: b.position))
