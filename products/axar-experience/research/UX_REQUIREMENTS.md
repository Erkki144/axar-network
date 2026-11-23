# AXAR experience engineering acceptance

## User archetypes

1. Zoo event kiosk: 5–15 high-frequency SKUs, one cashier, peak queue, flaky Wi-Fi.
2. Full-service restaurant: multi-table tickets, courses, kitchen routing, shared servers, split bill.
3. Retail counter: barcode SKUs, variants, inventory availability, returns and loyalty.
4. Owner: locations, exception queue, cash versus bank reconciliation, inventory, supplier invoices, accounting connections.

**Never mix owner financial complexity into cashier UI.** Progressive disclosure based on permissions and task.

## Target flows

- Express: open register -> tap product tile(s) -> Pay -> cash tender -> receipt. A normal 1-item cash sale target of <= 3 non-item taps after selection, including confirmation.
- Table: pick table -> select items/modifiers -> send to kitchen -> later close/split; item routing and previous sends remain visible.
- KDS: order received with source, station, timestamp, allergy tags, priority; only kitchen role can mark ready; no sound-only errors.
- Manager: start with exception inbox, cash differences and missing integrations, not dozens of charts.

## Accessibility and fatigue

- Minimum 48 CSS pixel primary touch targets and 8–12 px spacing; W3C WCAG 2.2 requirements plus a greater AXAR internal target.
- Large readable totals, sufficient contrast, tactile scanner/keyboard support, no color-only status.
- Persistent connection and register state indicator. Clear confirmation on final sale and refund; receipt accessible on-screen.
- Explicit offline modes: cash may function if durable local storage validated; card availability is processor-specific and must not be invented.
- On user error: clear undo/removal controls; never silently delete financial records.

## Research-based experiments to validate, not guarantees

- 20 operators, counterbalanced tasks between competitor and AXAR where licensing allows.
- Median and p90 completion time, observed wrong-item rate, retraining interventions and SUS questionnaire.
- Stress test 50 orders per minute, 10 registers, network interruption and printer failure.
- Acceptance is based on measured improvement rather than UI novelty.
