# AXAR EXPERIENCE — working cashier UX concept

A **functional local UI prototype**, not a connected POS. It never contacts banks, card processors or accounting APIs, persists no orders, and displays explicit *SIMULATED / DEMO* warnings. The interactions are usable in desktop, tablet and Android browser, subject to browser support.

## Run

```bash
cd products/axar-experience/prototype
python -m http.server 8765 --bind 127.0.0.1
# open http://127.0.0.1:8765
```

- Express product taps, live basket, quantity adjustments, category/search filters and simulated cash checkout with receipt.
- Restaurant tables and separate baskets, send to kitchen demo.
- Kitchen display (KDS) demo and simulated status changes.
- Supervisor example view with zero real metrics (labelled fictional).
- Responsive 390px/768px/desktop; keyboard shortcuts: `/` focuses search, Escape closes overlay.
- No account data, payments, card data, or analytics are transmitted.

See `research/UX_REQUIREMENTS.md` and benchmark file under `../axar-build/research/`.

## How to integrate safely

Use an authenticated session to pass POS catalog, registration context and money computed by FastAPI. Server is sole authority for tax and payment status. Client must never mark real card payment confirmed on its own. Add offline transactional local store and atomic queue before promising offline.
