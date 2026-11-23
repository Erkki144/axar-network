# Slice 3 — AXAR ONE cashier UI on the real POS API (issue #11)

Status: **local demo, simulated cash only.** Branch
`claude/axar-one-s5-integration-0r9ip8`, based on PR #7 `40c144b`. Nothing is
deployed. The demo header identity is still not authentication (#2).

## What was imported from PR #9 and what was not

| From AION's PR #9 (`products/axar-experience/prototype` @ `6c37a5d`) | Here |
| --- | --- |
| `styles.css` (layout, palette, responsive breakpoints) | copied unchanged → `axar_pos/static/one/styles.css` |
| `index.html` structure (sidebar, topbar, workspace, checkout panel, modal) | adapted → `static/one/index.html` (register bar, server quote state, honest footer) |
| `app.mjs` interaction design (modes, product grid, basket, tables, kitchen, manager) | **rewritten** → `static/one/app.mjs`; all data and money now come from the API |
| `money.mjs` (browser VAT, rounding, cart totals) | **not imported** — replaced by `format.mjs` (display/parse only) |
| hard-coded menu, fake receipt, fake manager numbers | removed |

PR #9 itself is untouched. Additions are in `static/one/integration.css`.

## Contract map (UI action → API)

| UI action | API call | Notes |
| --- | --- | --- |
| Load menu | `GET /api/v1/products` | new field `category` (menu grouping only) |
| Choose register | `GET /api/v1/registers` | choice remembered in `localStorage` (register id only) |
| Drawer status | `GET /api/v1/registers/{id}/drawer` | |
| Open drawer | `POST /api/v1/drawer/open` | float entered by cashier |
| Basket totals | **`POST /api/v1/sales/quote`** (new) | read-only, same pricing code as the sale, `binding: false` |
| Pay cash | `POST /api/v1/sales/cash` + `Idempotency-Key` | one key per attempt; retry re-sends same key + body |
| Receipt | `GET /api/v1/sales/{id}/receipt.txt` | server text, shown verbatim |
| Manager report | `GET /api/v1/reports/daily`, `GET /api/v1/sales?business_date=` | |
| Refund (partial/full) | `GET /api/v1/sales/{id}`, `POST /api/v1/sales/{id}/refunds` + key | demo `X-Role: manager` header |
| Refund receipt | `GET /api/v1/refunds/{id}/receipt.txt` | |
| Close drawer | `POST /api/v1/drawer/{id}/close` | two-step confirm, shows expected / counted / variance |
| Tables, kitchen tickets | — (none yet) | **DEMO, this browser only** (`localStorage`), labelled in UI |

Backend changes on this branch: `POST /api/v1/sales/quote` (pricing extracted
into `_price_lines`, shared by sale and quote), `Product.category`, extended
demo catalog, and the UI served at `/one/` (`/` redirects there; the earlier
simple UI stays at `/classic`). Static files are sent with
`Cache-Control: no-store`, `nosniff`, and `.mjs` as JavaScript.

## Payment state machine (client)

`input → sending → done` on server success (201 new / 200 replay).
- **Network error, timeout or 5xx → `unconfirmed`.** The server may have
  recorded the sale. The UI shows a warning and locks the tender, the basket,
  the table and the register. The only action is "Proovi uuesti (sama makse)",
  which re-sends the identical body with the same `Idempotency-Key`. No success
  is shown without a server response.
- **4xx → `error`** (definitely not recorded, e.g. drawer closed): the message
  is shown and the next attempt gets a new key.
- **Double tap:** a second click while `sending` is ignored (client guard). The
  server would also deduplicate it via the same key.
- Refunds follow the same machine with their own key.

## Acceptance tests (issue #11) → evidence

Browser tests: `tests_e2e/test_cashier_ui.py`. Each test runs a fresh uvicorn
with a fresh SQLite DB in headless Chromium and checks the **server state** via
the API, not just the screen.

| # | Issue #11 criterion | Test(s) | Result |
| --- | --- | --- | --- |
| 1 | Products from the API, tenant/register scoped | `test_catalog_comes_from_api_and_sale_uses_server_amounts` (UI names == API names) | pass |
| 2 | Server pricing, idempotency key, server receipt/change; double tap / retry never double-charge | same test (7,95 € / change 2,05 € / receipt nr 1); `test_double_tap_creates_exactly_one_sale` (1 request, 1 sale); `test_lost_response_after_commit_retry_returns_same_sale`; `test_network_failure_before_commit_then_retry` | pass |
| 3 | Register open/close via API, visible recoverable errors | `test_close_drawer_requires_confirmation_and_shows_variance`; `test_empty_cart_and_closed_drawer_block_payment`; `test_insufficient_tender_cannot_be_confirmed` | pass |
| 4 | Table order stays pending after kitchen send, no disappearance | `test_table_bill_survives_reload_and_is_paid_via_api` (survives reload, paid through the real API, 11,80 €) | pass — **device-local DEMO**, not cross-device |
| 5 | KDS durable or clearly labelled simulation | DEMO banners in tables/kitchen views (asserted in the table test) | labelled; no server KDS yet |
| 6 | 390 px and 1024 px, keyboard, labels, large targets, offline/failure states | `phone` (390×844) and `tablet` (1024×768) fixtures, no horizontal scroll, ≥ 44 px targets; `test_offline_is_visible_and_nothing_is_faked` | pass (see limits) |
| 7 | Contract/browser tests: sale, retry, empty cart, network failure, refund, two registers, rounding | all above plus `test_partial_refund_from_manager_view`, `test_two_registers_are_isolated`, `test_cash_rounding_shown_from_server` (+0,01 / −0,01) | pass |
| 8 | No fake payment, no public demo identity, no card/bank | client has no money authority (`test_ui_has_no_client_side_money_authority`); service binds 127.0.0.1 | pass |
| 9 | Click count / latency benchmark | see below | recorded |

**Mutation checks of the UI.** Each guard was removed in turn and the suite
re-run:
- retry with a **new** key → 2 tests fail;
- removed the double-tap guard → `test_double_tap…` fails;
- "success" shown on network error → 3 tests fail.

Backend: `tests/test_quote_and_ui_assets.py`:
- the quote equals the real sale for every amount;
- the quote writes nothing and needs no open drawer;
- tenant isolation (404), schema rejections and identity are checked;
- the UI files are served as JS with no-store;
- a guard test fails if browser-side VAT or rounding code reappears.

### Benchmark (local, headless Chromium, this session)

| Task | Clicks | Server round-trip |
| --- | --- | --- |
| 3-item cash sale with a quick-amount tender, drawer already open | **6** (3 products + pay + 10 € + confirm) | pay → receipt shown in **231 ms** (sale + receipt fetch, local) |

Low-cost Android hardware and real network latency are **not** measured.

## Known limits / follow-ups

- **Phone layout:** at 390 px the basket sits below the menu (prototype
  layout), so the cashier scrolls to pay. A sticky "Maksa X €" bar is the
  obvious next UX step.
- **Tables and kitchen** have no server model. Bills and tickets live in one
  browser's storage only. Cross-device restaurant use needs an order/ticket
  API (proposed next slice), authenticated per #2.
- **Identity:** the demo headers (`X-Tenant-ID`, `X-Cashier-ID`, `X-Role`) are
  sent by the page. Never expose this service publicly before #2.
- **Accessibility:** labels, live region, focus outlines and ≥ 44 px targets are
  in place. No automated WCAG audit (axe) has been run yet.
- **Receipt fetch failure** after a confirmed sale shows "payment saved,
  receipt failed". A reprint button in the manager view is still to do.
