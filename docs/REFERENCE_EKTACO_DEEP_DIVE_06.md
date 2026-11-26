# AXAR ONE — Ektaco süvauuring VI: CC5 juhendikeskusest väljapoole jäävad funktsioonid ja valdkondlikud seosed

**Lähteallikad:** Ektaco ametlik CompuCash toodete ülevaade, toitlustuse ja meelelahutuse kasutusjuhtude lehed ning originaalse CC5 juhendikeskuse kuvatõmmised. **Täpsuse skaala:** [M] = Ektaco turunduslehel nimetatud võimekus; [E] = ametlik juhend/pilt näitab konkreetset töövõtet; [AX] = meie kavandatav lahendus; [U] = päris Ektaco API/riistvara detailsed tingimused jäävad kinnitamata.
**Dokumentide katvus:** 64 kontori + 7 CC5 kassa indeksi artiklit avati ülevaatuse käigus. Muude Ektaco toodete, nagu CC Retail / CC4000, ja YouTube/Vimeo videokogu täielik läbivaatus ei ole selle 71-kirjelise korpuse sees. Ära nimeta 71 artiklit kogu Ektaco maailma absoluutseks ammendavaks katvuseks.

## 1. AXARi põhitoode vs tulevased vertikaalid

[M] Toote ülevaade nimetab lisaks POS müügile eraldi iseteenindust, makseterminalis toimivat kassaäppi, mobiilset juhi juhtimist, laua/QR tellimist, jootraha, kaalulahendusi, elektroonilisi saatelehti, menüü optimeerimise aruannet, vöötkoodide ja hinnasiltide loomist, piletite müüki, läbipääsusüsteeme, külastajastatistikat, CompuAccess tööaja arvestust ja videovalvet. Mõnda võimet kirjeldatakse tooteüleselt, mitte CC5 kassaspetsiifiliselt.

[AX] AXARi piiride põhimõte:
- CORE: Product/Price/Tax, Sale/Order, Payment/Refund, Stock/Recipe, Identity/Scope, Events/Accounting.
- CHANNEL ADAPTERS: cashier, waiter, QR, delivery, marketplace, self-service kiosk, partner API.
- HARDWARE ADAPTERS: integrated payment smart terminal, standalone EFT, ESC/POS printer, kitchen display, barcode reader, weighed goods scale, access reader/gate.
- OPTIONAL VERTICALS: zoo/museum tickets and timed entry, memberships, access validation, staff timeclock, crowd / visitor count. **Ära seo neid otse toidumüügi kassa baasprotsessiga**, enne kui tellija ärivajadus ja lepingud kinnitatakse.
- CONTROL: mobile manager actions as role- and company/outlet-scoped API commands with step-up for money/catalog/tender changes; no backdoor mobile permissions.

## 2. Füüsiline ja veebikanali tehing on sama äriline tuum, aga erinev risk

[AX] A self-service kiosk can accept guest touch input without cashier authentication, but never accepts untrusted client prices or arbitrary VAT. Device owns location and limited paid capabilities; payment result must be confirmed; refunds require authorized flow. It must be able to recover after power loss without charging twice. If customer abandons screen after terminal approval, an immutable paid sale and a receipt recovery flow must still exist. Timeouts cannot mean 'free goods' or 'double charge'.
[T] 100 kiosk transactions + power-off at every payment transition, max one capture and one sale per checkout id. A cash-only kiosk may not expose card feature or permit offline stored-value redemption.

## 3. Külastajapilet ja läbipääs (loomaaia jaoks võimalik hilisem lisa)

[M] Ektaco meelelahutuslehed nimetavad POS/veebipiletit, turnikeesid/käepaelu, läbipääsu ning külastajate aruandlust, kuid ei avalda täielikku andme- ega seadmelepingut.
[AX] `AdmissionTicket` issued from one sale line, `AccessEntitlement` with venue/zone/session, `GateScan` (ALLOW/DENY/UNKNOWN) as separately idempotent immutable events. A paid ticket may be used once at a single entrance only when policy says so; multi-entry requires different rule. QR token is signed/opaque, no invoice/customer PII in public barcode. Refund/reissue/reversal and offline gate policy must be explicit.
[T] A ticket QR scan twice simultaneously at two gates yields only one accepted entry under one-time entry policy. Refund revokes future entry without silently deleting original entry trace. Offline gates do not trust arbitrary reused unsigned tokens.
[U] Need confirmed gate hardware SDK, ticket legal terms and venue owner approval. AXARi zoo toitlustuskassale ei eeldata automaatselt loomaaia piletisüsteemi õigusi.

## 4. Jootraha, pandipakend ja kohustuslik eraldi arvestus

[M] Ektaco toodete leht nimetab jootraha jättmise. CC5 makse ekraani avalikul pildil on eraldi 'Pant' nimeline kirje, ent ainult ekraanipildist ei selgu selle seadistatud majanduslik või maksualane tähendus.
[AX] `TipAllocation` links to Payment/Cashier/Shift and rules for distribution, payroll, provider fees and tax; jootraha should not automatically be booked as product revenue or counted as menu-unit profitability. Tip refund must not cause stock return. Captured tip vs paid-out tip vs employee accrual are separate amounts.
[AX] `PackagingDeposit` or other returnable container liability must have exact policy, sale/refund and tender behavior, VAT treatment validated externally and hardware-return workflow if used. Do not infer from the screen how CC5 internally handles 'Pant'. In retail flows, product sale and deposit refund are distinct.
[T] €20 food sale + €3 tip → recorded payment €23, sales metric and tax base according to approved tip policy, zero spurious COGS for the tip. Deposit on bottle and subsequent return do not mutate historic food sale and reconcile as designed.

## 5. Kaalutooted ja vöötkoodid

[M] Ektaco product page mentions weight scales, barcodes/price label generation and handheld inventory terminals. API/scale message protocol and measurement legal compliance are not exposed.
[AX] `MeasuredSaleLine` includes source device calibration reference, integer quantity in base units, unit price and weighed price calculation with reviewed rounding. Barcode mapping distinguishes packaged sale SKU, internally printed variable-weight barcode, supplier GTIN, and internal inventory item. Reject silent conversion collision and mixed packaging units.
[T] 0.735 kg weighed food × 12.50€/kg is calculated in minor currency units with documented rounding, printed label scans to identical priced sale, inventory reduces 0.735kg, cost correct. No float accumulation and unauthorized changes to weight blocked.

## 6. POS ühes makseterminalis ja reaalsetest seadmetest sõltuvad töörežiimid

[M] Ektaco claims app running on a payment terminal plus standalone external printer and portable tills. Device specifics are NOT asserted.
[AX] SmartPOS capability matrix must distinguish device system OS, provider certification, deployment, receipt printer, cash drawer, kiosk mode, secure key store, device MDM, offline logs. Payment terminal is regulated PCI environment; do not load unrestricted scripts or keep cardholder data. If the payment app and POS share hardware, trust boundaries remain: provider receipt/transaction ID still required.
[T] One POS transaction on smart terminal paid then receipt printed (or queued with failure); app crash and restart: no second charge, receipts fetch same document; offline modes checked against provider legal/certified capability. No real claims before HIL/sandbox and contracts.

## 7. Chef/menüü optimeerimine: nõudluse juhtimine, mitte juhuslik retseptisoovitus

[M] Product page describes 'menüü optimeerimise aruanne' and purchase/sales forecasts; detailed formulas or economics not published. Current owner-created #38/#39 and underlying #14 should hold.
[AX] Explainable model: item contribution margin (price ex VAT - effective recipe COGS - variable packaging/platform fees), popularity, lead times, shelf life, capacity, weather/visitor demand. Recommendations must separate 'more profitable to promote' from 'inventory expires soon'. Consent/approval before price/menu publish or ordering.
[T] Predicted 1000 zoo visitors vs 5000 alters reorder proposal but never auto-posts purchase; view shows contributing demand factors and uncertainty bounds; sell-out duration handled.

## 8. AXAR ONE uued kontrollpunktid E45–E52

| Edge | Link | Acceptance / proof | Maturity |
| --- | --- | --- | --- |
| E45 | Kiosk guest checkout → POS sale/payment/receipt | hardware crash, one capture, one sale, print retry | Marketing-inspired [M], #18/#25 |
| E46 | Ticket issuance → permission → access scan → attendance analytics | unique ticket scan policy, refund and offline gate | Optional vertical, not yet required #31 |
| E47 | Card/employee tip → cash custody/payroll/liability | tips not duplicated into food sales or stock | Marketing-inspired [M], #32/#33 |
| E48 | Packaging deposit → issued/refunded financial/document flow | accountant validated VAT and returnable container policy | [M/picture], requires separate business decision |
| E49 | Scale/barcode → quantity-priced sale → exact stock consumption | exact units and rounding, device certification | [M], #25/#14 |
| E50 | SmartPOS deployment/device authority → provider capture/receipt | no PCI breach; no double charges and HIL test | [M], #25/#34 |
| E51 | Visitor forecasts + stock expiry → explainable procurement and menu suggestion | human-approved, no silent orders/prices | [M], #39/#14 |
| E52 | Mobile admin command → versioned policy → POS/warehouse/device | role-scoped, audit, MFA, propagation ACK | [M], #34/#32 |

## 9. Screenshots actually checked in public web view

- https://ektaco.ee/wp-content/uploads/2026/11/chrome-pjemap0rn6-860x288.png : QDS has separate 'TEGEMISEL'/'VALMIS' columns and clock, but no real active orders on sample.
- https://ektaco.ee/wp-content/uploads/2021/02/260202-16-44-26-dnplayer-860x460.png : CC5 shift screen has cash IN/OUT separate from finish staff/till shift.
- https://ektaco.ee/wp-content/uploads/2025/07/image-21-1.png : product master list with left category tree and right searchable/filterable columns.
- https://ektaco.ee/wp-content/uploads/2025/07/image-35-1.png : recipe gross quantity, waste %, net, ingredient and save in one form.
- https://ektaco.ee/wp-content/uploads/2021/11/pilt-860x499.png : original vendor document side-by-side with extracted invoice fields, re-read/cancel/save; visible 'auto-create future vendor invoices' opt-in.
- https://ektaco.ee/wp-content/uploads/2021/02/chrome-i5jiunsqnn.png : line-level invoice cancellation and restock checkbox, confirming refund/stock are distinct choices.
- https://ektaco.ee/wp-content/uploads/2021/02/260202-16-57-48-dnplayer-860x464.png : CC5 settings buttons refresh data, reset bills and reload full data, with support warning.
- https://ektaco.ee/wp-content/uploads/2021/02/260202-14-17-16-dnplayer-860x484.png : payment split-screen, cash/card/custom/transfer choices and tender input.
- https://ektaco.ee/wp-content/uploads/2025/07/HELP_CC5OFFICE_tellimuse_tooted.png : 50% advance selection and order line.
- https://ektaco.ee/wp-content/uploads/2021/02/chrome-b7hra4thni.png : modifier multiple selection min/max fields.
- https://ektaco.ee/wp-content/uploads/2021/02/chrome-8ambedfcwj.png : modifier selling-price linkage.
- https://ektaco.ee/wp-content/uploads/2025/07/image-7-17.png : WooCommerce multi-warehouse stock combine selection wizard.
- https://ektaco.ee/wp-content/uploads/2021/02/chrome-couhz9xsyy.png : Office/Checkout permissions tabs (not detailed rights).

**Unverified image links during this pass:** https://ektaco.ee/wp-content/uploads/2023/09/chrome-mmwwbaaqrp-522x860.png and https://ektaco.ee/wp-content/uploads/2024/09/kihqej5nc2-516x860.png returned errors in web image open; earlier research cited them, but this pass does NOT assert their visible pixel contents. Use text of the corresponding article as evidence instead.

## 10. Primary source level and rollout discipline

- Official help article + screenshot -> visible user workflows and settings, NOT proof of database/event implementation.
- Official marketing page -> capability name only, NOT proof of which CC5 version, feature licensing or measured reliability.
- Claude PR with mocks -> simulated behavior only, NOT live merchant/card/printer success.
- Real device + vendor integration contract + end-to-end tests + legal/accountant signoff -> independent pilot proof.

Source URLs:
https://ektaco.ee/product/compucash/
https://ektaco.ee/business_type/restoranid-kohvikud-ooklubid/
https://ektaco.ee/business_type/muuseumid-meelelahutuskeskused/
https://ektaco.ee/helpcenter/compucash5/
https://ektaco.ee/helpcenter/kassa/cc5-kassa/

**Do not merge, deploy, send real card payments or purchase services without owner approval. This document is research and acceptance criteria only.**
