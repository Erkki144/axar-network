# AXAR EXPERIENCE ↔ AXAR POS API integration contract (AION review, 2026-11-23)

This contract is derived from Claude's PR #7 `products/axar-pos/axar_pos/api.py` at commit `40c144bc`. **Not a claim of deployed integration.** Implement in issue #11, draft PR.

## Actual backend routes (do not invent endpoints)

| User task | API |
|---|---|
| Catalog | `GET /api/v1/products` |
| Registers | `GET /api/v1/registers` |
| Current drawer | `GET /api/v1/registers/{register_id}/drawer` |
| Open drawer | `POST /api/v1/drawer/open` |
| Close drawer | `POST /api/v1/drawer/{drawer_id}/close` |
| Cash sale | `POST /api/v1/sales/cash` |
| Sale detail | `GET /api/v1/sales/{sale_id}` |
| Receipt | `GET /api/v1/sales/{sale_id}/receipt.txt` |
| Simulated cash refund | `POST /api/v1/sales/{sale_id}/refunds` |
| Daily report | `GET /api/v1/reports/daily` |

The backend **does not currently provide** persisted restaurant tables, kitchen tickets, courses, modifiers, split checks, or order history outside completed sales. Do not misrepresent prototype's local-only state as server-persisted.

## Request invariants

- Cash sale body: `{register_id: integer, lines: [{product_id: integer, quantity: integer}], tendered_cents: integer}`.
- Client must supply an `Idempotency-Key` header (8–128 chars) and reuse the **same key** for a retry of the same sale. A changed cart/tender/register must use a new key; a replay with changed payload is a conflict.
- UI must use server-returned price, tax, due, change, receipt; never send client total or client VAT as authority.
- Disable pay button after first submit; on network timeout show **unconfirmed** state, not success or failure. Recover by replay with original key or fetch confirmed sale; never generate a new key automatically on retry.
- Register selection and drawer open state are persistent and visible. On close, show counted amount, expected amount and variance; prevent accidental close.
- Refund requires a separate key, original sale ID and role authorization; demo headers are NOT real authentication.
- Never write PII or payment secrets to browser localStorage or console. No real card payment until provider-confirmed integration.

## Test matrix for integration

1. One SKU sale: 7.95 EUR due, tender 10.00 EUR, 2.05 EUR change (where current seed catalog supports that case).
2. Rapid double-tap: exactly one backend sale and one drawer increment.
3. Timeout after commit: retry same key and payload returns the original sale, not a duplicate.
4. Retry same key with different quantity/tender/register: explicit conflict.
5. Different registers: isolated drawer and sales; no cross-tenant access.
6. Refund: duplicate key is safe; partial refund never exceeds remaining amount; no fake card refund.
7. Cash 5-cent rounding: UI uses server due and preserves rounding in receipt and daily report.
8. Network failure: clear non-destructive retry state, no success toast without backend confirmation.
9. Keyboard and 390px / 1024px viewport: no clipping; accessible error and busy status.
10. Kitchen and tables: explicitly labeled DEMO until persisted ticket API is built.

## Release gates

- Issue #2 authentication, RBAC and PostgreSQL migration must be reviewed before any public service deployment.
- Existing repo is PUBLIC and hosts unrelated marketing app; commercial POS belongs in a PRIVATE repo.
- Automated independent Codex/Gemini workflow on PR #9 currently SKIPPED; do not label it as a passed independent review.
- GitHub CI green + independently reviewed diff + human approval required for merging or deploying.
