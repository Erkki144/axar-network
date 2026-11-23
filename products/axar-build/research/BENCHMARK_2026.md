# AXAR ONE — comparative POS design research (22 November 2026)

Source type: vendor official feature and support documentation, not independent benchmark testing. Product claims are not necessarily validated. Do NOT assert AXAR is fastest or best without empirical UX and reliability studies.

| Product / geography | Demonstrated vendor capability | Design principle AXAR should adopt | Caveat |
|---|---|---|---|
| ERPLY (EE/global) | Offline-supported register, multi-store, API, inventory and transfer workflows | Separate branch-level and company-level operations, inventory actions from phone | Retail-first tasks can overload restaurant POS; hide from cashier |
| Ektaco CompuCash (EE) | Hospitality, KDS, queue screens, delivery integrations, self-service and accounting links | High fidelity local tax/restaurant logic + order channel unification | Feature depth is not evidence of easy first-day training |
| Toast (US/UK/global) | Table-side ordering/payment, KDS, robust guided offline modes | Handheld-first and explicit outage behavior | Offline card acceptance depends on hardware/provider, not just app |
| Square (US/UK) | QSR/full-service presets, item modifiers, split checks, floor plans, kiosks | Fast role-specific order-to-pay interactions | Multiple restaurant modes need predictable transitions |
| Lightspeed (UK/global) | Multi-location restaurant performance, tableside, QR payment, inventory | One owner dashboard across venues, location-level permissioned config | Complex reports should never appear in cashier view |
| Clover (US) | KDS, register/mini/dual screen, table service | Hardware-independent capability detection and graceful fallbacks | Vendor hardware ties limit portability |
| Epos Now (UK) | Hospitality kitchen order flow, apps and accounting integrations (Sage, Xero etc.) | Connector marketplace with status and recovery UX | Every connector needs version, scope, idempotency and health |
| Shopify POS (global) | Omnichannel orders and shared inventory/staff/customer data | Order/location inventory coherence across channels | Retail fulfillment models differ from restaurant meal courses |
| SumUp POS Pro (UK) | Simplified catalog, feature toggles, restaurant workflows | Start simple, unlock advanced functions only when enabled | Simplicity must be tested with actual cashiers |

## Original source evidence

- ERPLY: https://erply.com/features ; https://erply.ee/
- Ektaco: https://ektaco.ee/product/compucash/ ; https://ektaco.ee/business_type/restoranid-kohvikud-ooklubid/
- Toast: https://support.toasttab.com/en/article/Using-Toast-in-Offline-Mode ; https://pos.toasttab.com/products/point-of-sale
- Square: https://squareup.com/us/en/restaurants/capabilities ; https://squareup.com/us/en/point-of-sale/restaurants/pricing
- Lightspeed: https://www.lightspeedhq.com/uk/pos/restaurant/multilocation-restaurant-epos/
- Clover: https://www.clover.com/pos-solutions/full-service-restaurant
- Epos Now: https://www.eposnow.com/uk/systems/hospitality-pos/
- Shopify: https://www.shopify.com/pos/features
- SumUp: https://www.sumup.com/en-gb/point-of-sale-overview/pos-pro/features/
- WCAG: https://www.w3.org/WAI/WCAG22/quickref/

## Product strategy: do not clone

Create three **composable role-specific experiences** with one transaction model:

1. **EXPRESS** — kiosk/cafe/fast event, large favorites and single-tap adding; checkout and receipt always visible; optimized for high rush.
2. **HOSPITALITY** — floor plan, table/seat modifiers, courses, KDS, split bill, tips; fast navigation via held orders.
3. **RETAIL** — barcode-first search, variants, serials, stock lookup, returns, loyalty, e-commerce pickup.

Management/inventory/accounting is a distinct owner interface, not a tab accidentally exposed to cashiers. A user's permissions determine visibility, not obscurity of UI labels.

### Differentiation hypotheses for AXAR

- **One-tap recovery** from network failure; never imply card payment succeeded without processor authorization.
- **Cash path succeeds offline** (after transactional local store and synchronization are independently proven); no outbox duplicate post-sync.
- **Task-adaptive UI**: current step and next safe action visible; no arbitrary AI-generated button changes mid-shift.
- **Explainable accounting bridge**: each journal amount traceable to receipt, settlement and bank item, with exception reasons.
- **AI owner assistant**: preview reconciliation, suggest stock/order actions, require human approval for transfers/tax filing.
- **Modular mode switches**: one device may combine retail and café sales but retains explicit order contexts, registers and tax classification.

## UX validation plan

- Recruit operators (novices, experienced cashiers, managers) from multiple business types. Observe without coaching.
- Use standardized tasks: 3 items and tender; remove accidental item; table hold/split and send kitchen; failed network recovery; barcode out-of-stock; close drawer with shortage; correct previous sale.
- Baseline the same tasks on current deployed competitor software **where legally accessible**. No proprietary UI scraping/bypass; no claims about software not tested.
- Measure median, p90, wrong-tap rate, error recovery success, task completion, confidence, visual accessibility and manager onboarding burden.
- Only after measuring call a design faster, easier or better than a competitor.
