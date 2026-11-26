#61 P1-1a: product groups, product card, outlet price lists, four-eyes prices, R14

**Base:** `claude/card-terminal-flow-p03-0r9ip8` (#65). Stacked: merge #63 → #64 → #65 first.

## What changed
- `catalog.py`: one price source `price_for(product, outlet, at)`: outlet price list version → base version → legacy price (pre-P1-1a products only). "Not sold here" per list.
- Price versions: draft → approved | rejected | withdrawn, `valid_from`, never edited. **Four eyes:** the proposer can never approve; approval is tenant-wide only (owner / tenant-wide manager); approval never back-dates; the first approval of a legacy product writes its old price into history.
- Every pricing caller passes outlet + time: cash sale, quote, bill, offline sync, R11.
- **R14** (new reconciliation rule): every sale line price = the approved version valid for that outlet at that moment; four eyes intact.
- Events + schemas: `catalog.group.changed`, `catalog.product.changed`, `pricing.price.published`, `pricing.outlet.changed`. Tills reload their catalog live (SSE).
- Office tab "Tooted ja hinnad": groups, product card, barcodes, price proposals, approvals with preview, price lists, outlet assignment. Deactivation never touches stock.
- Migration `0009_catalog_pricing` (additive, RLS + grants).
- Docs: discovery (`docs/discovery/`), architecture 11–13, ADR-0016, roadmap.

## Tests
- `tests/test_catalog_pricing.py` (13), `tests/test_auth.py::test_catalog_roles_and_four_eyes_with_real_sessions`, `tests_e2e/test_catalog_ui.py` (office → four eyes → live till → outlet B price → old sale unchanged → R14 green).
- Local (Windows): all `tests` green; all browser tests green except two pre-existing POSIX-only ones (`test_durability`, `test_server_auth`, also failing on the base branch locally).
- PostgreSQL migration not run locally (no PG) — CI `test-postgres` must prove it.

## Not proven / known gaps
- **Edge (offline) tills do not pull prices yet** → P1-1d. An offline sale at an old price is quarantined (`price_mismatch`), never lost.
- **Migration number:** #71 also adds `0009`. Whichever merges second must become `0010` (down_revision `0009`).
- Weighed goods (P1-1b), recipe extension (P1-1c), price bulk job (P1-1e) are separate PRs.

No merge or deploy without the owner's approval.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
