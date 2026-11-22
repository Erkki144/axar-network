"""Money arithmetic in integer euro cents.

Invariant (ARCHITECTURE.md #1): the server computes every amount in integer
cents. Floats never enter a money path; inputs that are not ints are rejected
at the API schema layer.
"""

from __future__ import annotations

BASIS_POINTS = 10_000  # 100.00 %


def _div_round_half_up(numerator: int, denominator: int) -> int:
    """Integer division rounding half away from zero (commercial rounding)."""
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    sign = -1 if numerator < 0 else 1
    q, r = divmod(abs(numerator), denominator)
    if 2 * r >= denominator:
        q += 1
    return sign * q


def vat_from_gross(gross_cents: int, rate_bp: int) -> int:
    """VAT contained in a VAT-inclusive (gross) amount.

    vat = gross * rate / (1 + rate), rounded half-up to the cent.
    ``rate_bp`` is in basis points: 24 % == 2400.
    """
    if rate_bp < 0:
        raise ValueError("rate must be non-negative")
    return _div_round_half_up(gross_cents * rate_bp, BASIS_POINTS + rate_bp)


def round_cash(amount_cents: int, step_cents: int) -> int:
    """Round a cash amount to the nearest ``step_cents`` (half-up).

    Estonia rounds cash payment totals to 5 cents (Eesti Pank, from
    2025-01-01). Card payments are not rounded. ``step_cents`` of 1 disables.
    """
    if step_cents < 1:
        raise ValueError("step must be >= 1")
    return _div_round_half_up(amount_cents, step_cents) * step_cents


def format_eur(cents: int) -> str:
    """Render cents as an Estonian-style euro string, e.g. ``12,34 €``."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole},{frac:02d} €"
