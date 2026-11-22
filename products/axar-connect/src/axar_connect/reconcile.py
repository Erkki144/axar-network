"""Provider payout to bank credit reconciliation: exact reference matching only."""
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from .contracts import BankEntry, Payout


class Status(str, Enum):
    MATCHED = "matched"
    MISSING_BANK = "missing_bank"
    AMOUNT_MISMATCH = "amount_mismatch"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class Match:
    tenant_id: str
    payout_ref: str
    status: Status
    expected_cents: int
    actual_cents: int | None = None
    bank_transaction_id: str | None = None


def _ref(s: str):
    return " ".join(s.upper().strip().split())


def reconcile_payouts(payouts: list[Payout], bank: list[BankEntry]) -> list[Match]:
    """No matching on amount alone. Never mutates bank records or accounting books.

    Ambiguous entries are deliberately NOT treated as matched, even if amounts
    happen to be identical. Identity uses tenant, provider and payout ref.
    """
    payout_keys = [(p.tenant_id, p.provider, p.payout_ref) for p in payouts]
    if len(set(payout_keys)) != len(payout_keys):
        raise ValueError("duplicate provider payout IDs")
    bank_keys = [(b.tenant_id, b.account_id, b.transaction_id) for b in bank]
    if len(set(bank_keys)) != len(bank_keys):
        raise ValueError("duplicate bank transaction IDs")
    ref_uses = Counter((p.tenant_id, p.currency, _ref(p.payout_ref)) for p in payouts)
    result = []
    for p in payouts:
        key = (p.tenant_id, p.currency, _ref(p.payout_ref))
        candidates = [b for b in bank if
                      (b.tenant_id, b.currency, _ref(b.reference)) == key]
        if len(candidates) > 1 or ref_uses[key] > 1:
            result.append(Match(p.tenant_id, p.payout_ref, Status.AMBIGUOUS, p.bank_due_cents))
        elif not candidates:
            result.append(Match(p.tenant_id, p.payout_ref, Status.MISSING_BANK, p.bank_due_cents))
        else:
            b = candidates[0]
            status = Status.MATCHED if p.bank_due_cents == b.amount_cents else Status.AMOUNT_MISMATCH
            result.append(Match(p.tenant_id, p.payout_ref, status, p.bank_due_cents,
                                b.amount_cents, b.transaction_id))
    return result
