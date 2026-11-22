"""Balanced bookkeeping previews. No calls to any accounting provider."""
from dataclasses import dataclass
from hashlib import sha256
import json
from .contracts import CashSale


@dataclass(frozen=True)
class Posting:
    account: str
    debit_cents: int
    credit_cents: int

    def __post_init__(self):
        if not self.account or min(self.debit_cents, self.credit_cents) < 0:
            raise ValueError("invalid posting")
        if not (self.debit_cents == 0) != (self.credit_cents == 0):
            raise ValueError("postings must have one nonzero side")


@dataclass(frozen=True)
class Journal:
    tenant_id: str
    source_event_id: str
    source_sale_id: str
    journal_id: str
    postings: tuple[Posting, ...]

    def __post_init__(self):
        if sum(p.debit_cents for p in self.postings) != sum(p.credit_cents for p in self.postings):
            raise ValueError("unbalanced journal")

    def document(self):
        return {
            "tenant_id": self.tenant_id, "source_event_id": self.source_event_id,
            "source_sale_id": self.source_sale_id, "journal_id": self.journal_id,
            "currency": "EUR", "postings": [vars(p) for p in self.postings],
        }


def preview_cash_journal(sale: CashSale, *, cash_account: str, revenue_account: str,
                         vat_accounts: dict[int, str], rounding_gain_account: str,
                         rounding_loss_account: str) -> Journal:
    """Account mapping belongs to verified merchant's accountant, not AXAR defaults."""
    if not all((cash_account, revenue_account, rounding_gain_account, rounding_loss_account)):
        raise ValueError("all account mappings required")
    vat_by_rate: dict[int, int] = {}
    for line in sale.lines:
        vat_by_rate[line.vat_rate_bp] = vat_by_rate.get(line.vat_rate_bp, 0) + line.vat_cents
    for rate in vat_by_rate:
        if rate not in vat_accounts or not vat_accounts[rate]:
            raise ValueError(f"missing VAT account for rate {rate}")

    p = []
    if sale.cash_due_cents:
        p.append(Posting(cash_account, sale.cash_due_cents, 0))
    if sale.net_cents:
        p.append(Posting(revenue_account, 0, sale.net_cents))
    for rate in sorted(vat_by_rate):
        if vat_by_rate[rate]:
            p.append(Posting(vat_accounts[rate], 0, vat_by_rate[rate]))
    if sale.rounding_cents > 0:
        p.append(Posting(rounding_gain_account, 0, sale.rounding_cents))
    elif sale.rounding_cents < 0:
        p.append(Posting(rounding_loss_account, -sale.rounding_cents, 0))
    if not p:
        raise ValueError("zero-value sale not a posting")
    uid = sha256(f"axar-connect:v1:{sale.tenant_id}:{sale.event_id}:cash".encode()).hexdigest()[:32]
    return Journal(sale.tenant_id, sale.event_id, sale.sale_id, uid, tuple(p))


def canonical_document(journal: Journal):
    return json.dumps(journal.document(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
