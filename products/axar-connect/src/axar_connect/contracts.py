"""AXAR CONNECT business contracts. Amounts are integer euro cents."""
from dataclasses import dataclass
from datetime import date


def _int(n, field):
    if type(n) is not int or not -10**12 <= n <= 10**12:
        raise ValueError(f"{field}: expected bounded integer cents")


def _id(s, field):
    if not isinstance(s, str) or not s.strip():
        raise ValueError(f"{field} required")


@dataclass(frozen=True)
class SaleLine:
    gross_cents: int
    net_cents: int
    vat_cents: int
    vat_rate_bp: int

    def __post_init__(self):
        for f in ("gross_cents", "net_cents", "vat_cents", "vat_rate_bp"):
            _int(getattr(self, f), f)
        if min(self.gross_cents, self.net_cents, self.vat_cents) < 0:
            raise ValueError("refund must be separate, not a negative sale line")
        if self.net_cents + self.vat_cents != self.gross_cents:
            raise ValueError("line total mismatch")
        if not 0 <= self.vat_rate_bp <= 10000:
            raise ValueError("invalid VAT rate")


@dataclass(frozen=True)
class CashSale:
    tenant_id: str
    event_id: str
    sale_id: str
    register_id: str
    business_date: date
    receipt_no: int
    lines: tuple[SaleLine, ...]
    gross_cents: int
    net_cents: int
    vat_cents: int
    rounding_cents: int
    cash_due_cents: int
    currency: str = "EUR"

    def __post_init__(self):
        for f in ("tenant_id", "event_id", "sale_id", "register_id"):
            _id(getattr(self, f), f)
        if type(self.business_date) is not date or type(self.receipt_no) is not int or self.receipt_no < 1:
            raise ValueError("invalid business date or receipt number")
        if self.currency != "EUR" or not isinstance(self.lines, tuple) or not self.lines:
            raise ValueError("EUR and at least one immutable sale line required")
        for f in ("gross_cents", "net_cents", "vat_cents", "rounding_cents", "cash_due_cents"):
            _int(getattr(self, f), f)
        if (self.gross_cents, self.net_cents, self.vat_cents) != (
            sum(l.gross_cents for l in self.lines),
            sum(l.net_cents for l in self.lines),
            sum(l.vat_cents for l in self.lines),
        ):
            raise ValueError("sale totals do not equal line snapshots")
        if self.cash_due_cents != self.gross_cents + self.rounding_cents or self.cash_due_cents < 0:
            raise ValueError("invalid rounding or cash due")
        if not -4 <= self.rounding_cents <= 4:
            raise ValueError("rounding outside expected 5-cent cash range")


@dataclass(frozen=True)
class Payout:
    tenant_id: str
    provider: str
    payout_ref: str
    gross_cents: int
    fee_cents: int
    refund_cents: int
    bank_due_cents: int
    currency: str = "EUR"

    def __post_init__(self):
        for f in ("tenant_id", "provider", "payout_ref"):
            _id(getattr(self, f), f)
        for f in ("gross_cents", "fee_cents", "refund_cents", "bank_due_cents"):
            _int(getattr(self, f), f)
        if self.currency != "EUR" or min(self.gross_cents, self.fee_cents, self.refund_cents) < 0:
            raise ValueError("invalid payout currency or negative components")
        if self.gross_cents - self.fee_cents - self.refund_cents != self.bank_due_cents:
            raise ValueError("payout net mismatch")


@dataclass(frozen=True)
class BankEntry:
    tenant_id: str
    transaction_id: str
    account_id: str
    reference: str
    amount_cents: int
    currency: str = "EUR"

    def __post_init__(self):
        for f in ("tenant_id", "transaction_id", "account_id"):
            _id(getattr(self, f), f)
        _int(self.amount_cents, "amount_cents")
        if self.currency != "EUR" or not isinstance(self.reference, str):
            raise ValueError("invalid bank entry currency/reference")
