"""Strict adapter from the actual Claude POS pos.refund.completed v1 event.

The caller must obtain the envelope through authenticated internal transport.
SHA-256 catches corruption but does not authorize a sender.
"""
import hashlib
import json
from datetime import date

from .pos_adapter import _integer, _unique_pairs
from .refunds import CashRefund, CashRefundLine


def from_pos_refund_outbox_v1(envelope: dict) -> CashRefund:
    if not isinstance(envelope, dict):
        raise TypeError("expected internal event envelope")
    if (envelope.get("type") != "pos.refund.completed"
            or type(envelope.get("version")) is not int
            or envelope.get("version") != 1
            or envelope.get("source") != "axar-pos"):
        raise ValueError("unrecognized refund event type/version/source")
    raw = envelope.get("payload")
    if not isinstance(raw, str):
        raise ValueError("refund event payload must be JSON text")
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != envelope.get("payload_hash"):
        raise ValueError("refund payload checksum mismatch")
    data = json.loads(raw, object_pairs_hook=_unique_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON constant")))
    if (not isinstance(data, dict) or data.get("payment_method") != "cash"
            or data.get("simulated") is not True):
        raise ValueError("expected explicitly simulated cash refund")
    if not isinstance(data.get("lines"), list) or not data["lines"]:
        raise ValueError("refund lines missing")
    lines: list[CashRefundLine] = []
    for item in data["lines"]:
        if not isinstance(item, dict):
            raise ValueError("invalid refund line object")
        lines.append(CashRefundLine(
            sku=item["sku"],
            quantity=_integer(item["quantity"], "quantity"),
            gross_cents=_integer(item["gross_cents"], "gross_cents"),
            vat_cents=_integer(item["vat_cents"], "vat_cents"),
            vat_rate_bp=_integer(item["vat_rate_bp"], "vat_rate_bp"),
        ))
    register_id = _integer(data["register_id"], "register_id")
    if register_id < 1:
        raise ValueError("invalid refund register ID")
    return CashRefund(
        tenant_id=envelope["tenant_id"],
        event_id=envelope["id"],
        refund_id=data["refund_id"],
        original_sale_id=data["original_sale_id"],
        register_id=str(register_id),
        business_date=date.fromisoformat(data["business_date"]),
        refund_no=_integer(data["refund_no"], "refund_no"),
        lines=tuple(lines),
        gross_cents=_integer(data["total_cents"], "total_cents"),
        net_cents=_integer(data["net_cents"], "net_cents"),
        vat_cents=_integer(data["vat_cents"], "vat_cents"),
        rounding_cents=_integer(data["cash_rounding_cents"], "cash_rounding_cents"),
        cash_paid_cents=_integer(data["cash_paid_cents"], "cash_paid_cents"),
    )
