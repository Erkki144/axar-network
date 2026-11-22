"""Immutable cash-refund contract and balanced bookkeeping preview.

This module rejects malformed POS cash-refund events, and NEVER initiates a
refund, a bank payment, an accounting export or an external API request.

Important: refund VAT is a *reversal allocation* of an earlier tax snapshot,
not necessarily vat_from_gross(refund_gross, rate) for every partial refund.
Cumulative per-sale caps and legal cash-rounding policy remain POS concerns.
"""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256

from .contracts import _id, _int
from .journal import Journal, Posting


@dataclass(frozen=True)
class CashRefundLine:
    sku: str
    quantity: int
    gross_cents: int
    vat_cents: int
    vat_rate_bp: int

    @property
    def net_cents(self) -> int:
        return self.gross_cents - self.vat_cents

    def __post_init__(self) -> None:
        _id(self.sku, "sku")
        for field in ("quantity", "gross_cents", "vat_cents", "vat_rate_bp"):
            _int(getattr(self, field), field)
        if self.quantity < 1 or self.gross_cents < 1:
            raise ValueError("refund line must reverse positive quantity and gross")
        if not 0 <= self.vat_cents <= self.gross_cents:
            raise ValueError("refund VAT must be between zero and refunded gross")
        if not 0 <= self.vat_rate_bp <= 10000:
            raise ValueError("invalid historical VAT rate")
        # Partial refunds may allocate tax fractionally across units. The
        # remaining fractional cent belongs to a later refund, hence no
        # independent single-refund VAT recalculation here.


@dataclass(frozen=True)
class CashRefund:
    tenant_id: str
    event_id: str
    refund_id: str
    original_sale_id: str
    register_id: str
    business_date: date
    refund_no: int
    lines: tuple[CashRefundLine, ...]
    gross_cents: int
    net_cents: int
    vat_cents: int
    rounding_cents: int
    cash_paid_cents: int
    currency: str = "EUR"

    def __post_init__(self) -> None:
        for field in ("tenant_id", "event_id", "refund_id", "original_sale_id", "register_id"):
            _id(getattr(self, field), field)
        if type(self.business_date) is not date or type(self.refund_no) is not int or self.refund_no < 1:
            raise ValueError("invalid refund business date or receipt number")
        if self.currency != "EUR" or not isinstance(self.lines, tuple) or not self.lines:
            raise ValueError("EUR and immutable refund lines required")
        for field in ("gross_cents", "net_cents", "vat_cents", "rounding_cents", "cash_paid_cents"):
            _int(getattr(self, field), field)
        if self.gross_cents <= 0 or self.cash_paid_cents < 0:
            raise ValueError("invalid gross or cash refund")
        if (self.gross_cents, self.net_cents, self.vat_cents) != (
            sum(line.gross_cents for line in self.lines),
            sum(line.net_cents for line in self.lines),
            sum(line.vat_cents for line in self.lines),
        ):
            raise ValueError("refund totals do not match immutable lines")
        if self.cash_paid_cents != self.gross_cents + self.rounding_cents:
            raise ValueError("cash refund does not match gross plus rounding")
        if not -4 <= self.rounding_cents <= 4 or self.cash_paid_cents % 5:
            raise ValueError("invalid 5-cent cash rounding")


def preview_cash_refund_journal(
    refund: CashRefund,
    *,
    cash_account: str,
    revenue_account: str,
    vat_accounts: dict[int, str],
    rounding_gain_account: str,
    rounding_loss_account: str,
) -> Journal:
    """Reversal preview; not an exported ledger entry.

    Positive cash rounding on a refund costs the merchant (expense/debit).
    Negative cash rounding on a refund benefits the merchant (gain/credit).
    An accountant must approve account names and tax correction policy.
    """
    if not all((cash_account, revenue_account, rounding_gain_account, rounding_loss_account)):
        raise ValueError("cash, revenue and rounding accounts are required")
    by_rate: dict[int, int] = {}
    for line in refund.lines:
        by_rate[line.vat_rate_bp] = by_rate.get(line.vat_rate_bp, 0) + line.vat_cents
    for rate in by_rate:
        if rate not in vat_accounts or not vat_accounts[rate]:
            raise ValueError(f"missing VAT reversal account for {rate}")

    p: list[Posting] = []
    if refund.net_cents:
        p.append(Posting(revenue_account, refund.net_cents, 0))
    for rate, vat in sorted(by_rate.items()):
        if vat:
            p.append(Posting(vat_accounts[rate], vat, 0))
    if refund.rounding_cents > 0:
        p.append(Posting(rounding_loss_account, refund.rounding_cents, 0))
    elif refund.rounding_cents < 0:
        p.append(Posting(rounding_gain_account, 0, -refund.rounding_cents))
    if refund.cash_paid_cents:
        p.append(Posting(cash_account, 0, refund.cash_paid_cents))
    if not p:
        raise ValueError("zero-value refund not journalable")
    uid = sha256(f"axar-connect:v1:{refund.tenant_id}:{refund.event_id}:cash-refund".encode()).hexdigest()[:32]
    return Journal(refund.tenant_id, refund.event_id, refund.original_sale_id, uid, tuple(p))
