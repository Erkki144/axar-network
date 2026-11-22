"""Strict AXAR POS `pos.sale.completed` v1 outbox -> CashSale contract.

This is a *trusted internal envelope* adapter. Caller MUST read envelope from the
authenticated POS database / signed event transport, never user-supplied JSON.
"""
import hashlib
import json
from datetime import date
from .contracts import CashSale, SaleLine


def _unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _integer(v, label):
    if type(v) is not int:
        raise ValueError(f"{label} must be integer")
    return v


def from_pos_outbox_v1(envelope: dict) -> CashSale:
    """Checks type/version/source/hash and financial identities; never posts a journal.

    Expected envelope is a DB-mapped `OutboxEvent` with keys `id`, `tenant_id`,
    `type`, `version`, `source`, `payload` (JSON text), `payload_hash`.
    The current v1 payload does not contain currency; POS v1 supports EUR.
    """
    if not isinstance(envelope, dict):
        raise TypeError("expected internal event envelope")
    if (envelope.get("type") != "pos.sale.completed"
        or type(envelope.get("version")) is not int
        or envelope.get("version") != 1
        or envelope.get("source") != "axar-pos"):
        raise ValueError("unrecognized event type/version/source")
    raw = envelope.get("payload")
    if not isinstance(raw, str):
        raise ValueError("event payload must be JSON text")
    actual_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if actual_hash != envelope.get("payload_hash"):
        raise ValueError("POS payload checksum mismatch")
    data = json.loads(raw, object_pairs_hook=_unique_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON constant")))
    if not isinstance(data, dict) or data.get("payment_method") != "cash":
        raise ValueError("expected cash-sale object")
    if not isinstance(data.get("lines"), list) or not data["lines"]:
        raise ValueError("missing sales lines")
    lines = tuple(SaleLine(
        gross_cents=_integer(x["gross_cents"], "gross_cents"),
        vat_cents=_integer(x["vat_cents"], "vat_cents"),
        net_cents=_integer(x["gross_cents"], "gross_cents") - _integer(x["vat_cents"], "vat_cents"),
        vat_rate_bp=_integer(x["vat_rate_bp"], "vat_rate_bp"),
    ) for x in data["lines"])
    register_id = _integer(data["register_id"], "register_id")
    if register_id <= 0:
        raise ValueError("invalid register ID")
    return CashSale(
        tenant_id=envelope["tenant_id"],event_id=envelope["id"],
        sale_id=data["sale_id"],register_id=str(register_id),
        business_date=date.fromisoformat(data["business_date"]),
        receipt_no=_integer(data["receipt_no"], "receipt_no"),
        lines=lines,gross_cents=_integer(data["total_cents"], "total_cents"),
        net_cents=_integer(data["net_cents"], "net_cents"),
        vat_cents=_integer(data["vat_cents"], "vat_cents"),
        rounding_cents=_integer(data["cash_rounding_cents"], "cash_rounding_cents"),
        cash_due_cents=_integer(data["cash_due_cents"], "cash_due_cents"),
    )
