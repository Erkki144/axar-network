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

## Review round 1 (AION P0): crash-safe pending operations

**Defect (confirmed):** the idempotency key of an unconfirmed payment or refund
lived only in page memory. Consider this sequence: the server commits, the
response is lost, then the tab reloads or crashes. The pending key was gone,
so the cashier could rebuild the cart and pay again with a new key, a
**duplicate sale** (or a duplicate cash payout for refunds). The new tests
below fail on the old code (5 of 6; the sixth passed trivially).

**Fix: a write-ahead journal in `localStorage` (`axar.one.pending.v1`).**
- **Before** any sale or refund POST, one versioned record is stored with:
  - operation type, register, API path;
  - the immutable request body and the Idempotency-Key;
  - the cart key or sale id, and the timestamp.

  It holds no PII and no card data. It is read back to verify. **If it cannot
  be written, the request is not sent**, and payments stay disabled while
  storage is broken.
- The record is cleared **only** after a definitive server answer: 2xx (cart
  cleared first) or 4xx (not recorded). Network errors, timeouts and 5xx keep it.
- **On boot, and when another tab writes it**, a valid record opens a
  **blocking recovery screen**: it cannot be closed, and sales, register
  changes and table switches are locked. Its only action re-sends the **same
  body and key**, so the server either records the operation once or replays
  the original.
- An **unreadable record** blocks the till. It can only be discarded through
  a two-step confirmation, after checking today's sales in the manager view.
- **Second tab:** both tabs can only retry the same key, so there is never a
  second record. A tab refuses to start a new operation while another tab's
  record exists.

Tests in `tests_e2e/test_crash_recovery.py` (7):
- sale committed → response dropped → **hard reload** → recovery → same key →
  exactly 1 sale and 1 drawer increment;
- the same for a refund → exactly 1 payout;
- a definitive 409 clears the record;
- a corrupted record blocks until a two-step discard;
- storage unavailable → the pay button stays locked even after the server quote;
- storage failing at submit → **no request sent**;
- second tab recovers, then the first tab retries → still 1 sale.

Mutation checks:
- no recovery on boot → 4 tests fail;
- sending despite a failed journal write → 1–2 tests fail.

**Limits of browser-only recovery** (documented, not solved here):
- The journal is per browser profile. A crash followed by a switch to
  **another device** cannot be recovered client-side; the cashier must check
  the manager view.
- The server-side idempotency key is the real guarantee. A server
  "operation status by key" endpoint, and server-side open orders, would make
  recovery device-independent (proposed for the orders slice).
- A cashier can still clear site data by hand. Nothing in a browser prevents
  that.

## Review round 2 (AION): ordering, cross-tab atomicity, uncertain 4xx

All three findings were confirmed. 11 new tests in
`tests_e2e/test_crash_recovery_round2.py` were run against the round-1 code:
**10 failed**. One of them produced **2 real sales** when two tabs paid the
same table at once.

1. **Cleanup before journal delete.** Every money operation (sale, refund,
   recovery) now runs through one function, `runMoneyOperation`, in this
   order:
   - journal check and write;
   - POST with the same key;
   - local cleanup (the table bill is saved **and read back**);
   - only then, journal delete.

   If the cleanup cannot be persisted, the journal stays, payments lock, and
   the receipt shows a warning; a reload then finishes via a same-key replay.
   Recovery used to delete the journal before cleanup; it now uses the same
   ordered path. `saveDemo()` no longer swallows errors.
2. **Cross-tab atomicity.** The whole operation runs under an origin-wide
   **Web Lock** (`navigator.locks`, `ifAvailable`). A second tab gets "teises
   aknas on rahatehing pooleli" and sends nothing. Without Web Locks,
   payments are **disabled** (fail closed). When another tab deletes the
   journal or changes table bills (`storage` event), this tab reloads the
   durable table state instead of trusting its memory.
3. **Only proven rejections are definitive.** The server looks up the
   Idempotency-Key before any business check. Therefore only these answers
   prove "not recorded" and clear the journal:
   - `422`;
   - `no_open_drawer`, `insufficient_tender`, `insufficient_drawer_cash`,
     `over_refund`, `invalid_refund`, `not_found`.

   Network errors, 5xx, **401, 403, 429, 409 `idempotency_key_reused` or
   `conflict`**, and unknown answers keep the journal; only a same-key retry
   is allowed.
4. **Strict journal schema.**
   - A read failure is `unreadable` (fail closed), not "no record".
   - A record must match the exact schema (allowed keys, integer ranges,
     line shapes).
   - Its operation must match its path: sale ↔ `/sales/cash`; refund ↔
     `/sales/{saleId}/refunds` with the same `saleId`.
   - Anything else is `corrupt`, so the till blocks.

   The corrupt-record discard is labelled as **DEMO, not a manager
   authorization** (that needs #2), and it verifies the record is really gone.

Tests:
- cleanup failure keeps the journal → reload → replay → 1 sale, bill cleared;
- crash after cleanup before delete → replay → 1 sale;
- no Web Locks → payments disabled;
- lock held by another tab → refused, 0 requests;
- **simultaneous payment of the same table from 2 tabs → exactly 1 sale and
  1 drawer increment** (round-1 code: 2 sales);
- lost response then 401 / 403 / 429 / 409-reused → journal kept → replay →
  1 sale;
- op/path mismatch → corrupt;
- unreadable storage → blocked.

**Mutation check:** without the lock, the two-tab and lock-held tests fail.

Still out of reach for a browser:
- a crash followed by **another device**;
- site data cleared by hand.

The proper fix is server-side operation status by key plus persistent open
orders (next slice).

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
