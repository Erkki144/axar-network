# AXAR CONNECT independent agent rules

- This is additive, independently reviewable AION work. Do not edit Claude's
  `products/axar-pos/` or AXAR Network marketing application.
- No external service credentials, real account identifiers, live transfers,
  real posting, user data or production deployment.
- Financial invariants: integer cents, tenant isolation, immutable source
  events, balanced debits/credits, explicit refund and rounding handling,
  idempotent retries; no guessing payout matches from amounts.
- Before PR: run `python -m pytest -q`; report exact count, command and
  interpreter; request independent human/code review for financial correctness.
- Don't claim simulations are authorized banking or accounting integrations.
- Next: Claude or an integration engineer should adapter-map POS v1 events and
  add provider sandbox test vectors on a separate branch.
