# AXAR ONE — Operating Model and Architecture v1.0
Status: PROPOSED, not deployed. Owner: Erkki. Date: 2026-11-25. Scope: POS + multi-entity + multi-site + inventory + kitchen + finance. Decision record for issue #27. Existing PRs remain unmerged until dependency and test review.

## 1. Non-negotiable invariants
1. A legal entity owns goods, owes tax, sells to the customer and holds a financial ledger. A physical site/building/outlet is not a legal entity.
2. Every sale references exactly one legal seller, site, outlet, register, cashier shift, price snapshot and tax snapshot. IDs remain immutable; names and addresses are historical snapshots.
3. Every stock quantity/value change has an append-only, idempotent movement and document; no manual direct balance edits. Balances are projections, rebuilt from movements.
4. Transfers within one legal entity are inventory movements, not sales. Cross-entity movements require explicit intercompany documents and tax/accounting treatment.
5. Order, kitchen ticket, payment attempt, payment capture/settlement, fiscal receipt, inventory consumption and accounting posting are distinct state machines. Never treat terminal timeout as decline or success.
6. Central product catalog, optional shared recipes, site/outlet-specific menus, prices, tax configuration, stock source, devices, roles and kitchen routes.
7. No production card charging, financial posting, hardware pairing, paid AI OCR or deployment without separate approval and test evidence.

## 2. Hierarchy and ownership
Workspace/tenant (security boundary)
  -> LegalEntity (registry number, VAT status, accounting books, currencies, invoice sequences)
  -> Site (physical address / campus e.g. Tallinna Loomaaed)
     -> optional Building / Zone (physical navigation only)
     -> SalesOutlet (stable code/name, cost center, hours, menu and stock sourcing policy)
        -> Register (device identity, drawer, shift, payment terminal, receipt printer)
     -> KitchenStation (may serve several outlets)
Warehouses/storage locations form a separate graph: central warehouse, site warehouse, outlet store, fridge, production area, in-transit, quarantine; they reference physical site and legal stock owner independently. An outlet maps each SKU/recipe ingredient to an allowed consumption source; one central warehouse can supply 10 outlets. Optional multiple legal entities can operate at same site, but access and ledger remain isolated.

Canonical identifiers: tenant_id, legal_entity_id, site_id, building_id nullable, outlet_id, register_id, storage_location_id, sku_id, recipe_version_id, order_id, sale_id, payment_attempt_id, provider_tx_id, stock_movement_id, journal_id, correlation_id. Never key business rules by display name or address.

## 3. Core data domains
Identity: tenants, legal_entities, memberships, roles, site_scopes, outlet_scopes, register_devices, employee_shifts, audit_events.
Places: sites, buildings, zones, outlets, outlet_registers, kitchen_stations, routing_rules, device_profiles.
Catalog: suppliers, supplier_items, SKUs, barcodes, pack/UOM conversions, price lists, VAT classifications, allergens, menu versions, recipe versions and BOM lines, substitutions.
Procurement: purchase_orders, delivery_documents, document_images, extraction_runs, extraction_lines, goods_receipts, receipt_lines, supplier_invoices, invoice_matching and exceptions.
Inventory: stock_locations, lot/batch/expiry, movements, transfer_orders, dispatch/receipt, reservations, stocktakes, waste, production_batches, costing layers.
Sales: carts, orders, order_lines, kitchen tickets, sales, sale_lines, bills, tender lines, receipts, returns, discounts and promotions.
Payments: payment_intents/attempts, terminal provider adapters, statuses, authorizations/captures/refunds, bank transfer receivables, reconciliation records.
Finance: immutable financial events, journals, posting rules, VAT lines, supplier payable, customer receivable, cash drawer ledger, bank statement matches, accounting export.
Ops: outbox/inbox, idempotency keys, local device bridge, printer jobs, device health, alerts, sync checkpoints, backups.

Use UUID/ULID for internal IDs, unique scoped human codes, composite foreign keys or validated constraints to prevent cross-entity links; tenant RLS and legal-entity authorization. Money integer minor units + currency; quantities fixed decimal with UOM; UTC timestamps plus site timezone; decimal tax/rounding rules versioned.

## 4. Event and workflow boundaries
- delivery_document.uploaded -> AI extraction DRAFT with image evidence and per-line confidence; manager corrects/approves -> goods_receipt.posted -> inventory movement; supplier invoice is separate and matched (2/3-way).
- internal transfer requested -> picked -> dispatched (in-transit) -> received/variance -> stock movement; no VAT/revenue for same owner.
- production: ingredients consumed by approved recipe version -> finished goods produced, yield/waste recorded; expiry/lot traceability.
- POS order: accepted -> kitchen ticket dispatched once per line version -> kitchen status; changes create delta ticket with original reference, not silent overwrite.
- bill: open -> one or many tenders; cash captured on confirmation, card only on authenticated terminal/provider result, transfer pending until bank reconciliation; only settle when confirmed tenders sum to due, accounting for refunds.
- sale finalization -> inventory consumption once, immutable price/tax/recipe snapshot -> finance posting via outbox/inbox with retries and deduplication.
- receipt and kitchen printing are distinct jobs and templates; may share one printer, route with durable queue, warn when physical print unknown, audited reprint.
- shift close -> cash count, variance, POS vs terminal vs bank reconciliation; manager review and audit.

Event envelope: event_id, event_type, schema_version, occurred_at, tenant_id, legal_entity_id, site_id, outlet_id nullable, aggregate_id, aggregate_version, correlation_id, causation_id, idempotency_key, payload. Outbox in same DB transaction as business state. Consumers inbox dedupe; retry + DLQ + manual repair, no cross-tenant leakage. Ordered per aggregate, not global order.

## 5. Example: zoo
Tuuleveski owns central Zoo warehouse and 10 named outlets (actual names supplied/imported by manager, never invented). Receive 200 bottled drinks into central; dispatch 40 to outlet A; outlet receives 40; sell 12 at outlet A: central 160, outlet A 28, total 188, ignoring other moves. A second register in outlet A shares the same stock source with concurrency-safe deductions. Outlet B unchanged. Cross-company consolidated reports sum monetary figures with entity dimensions and avoid internal transfer double-counting; stock valuation remains legally owned.

## 6. Permissions
Owner/system admin: workspace setup; entity finance admin: posting and VAT; site manager: menus and devices for site; outlet manager: stock and shifts for outlet; cashier: permitted sales and tender; kitchen: tickets only; stock receiver: goods intake; accountant: journal/reconciliation; auditor: read-only. Separate approval for refunds, stock adjustments, template publication, price overrides and device pairing. Every privileged action audited.

## 7. Interfaces
Cashier: outlet and register bound to session, simple 'Sularaha', 'Kaart', 'Ülekanne' buttons, remaining amount; no provider-specific UI. Kitchen: delta tickets with modifiers and timestamps. Manager: company/site/building/outlet tree, stock transfer screen, delivery photo inbox with checkboxes, printer/terminal registry, 58/80mm receipt designer, cash close, discrepancy queue. Reports filter company/site/building/outlet/register and date; preserve historical dimensions. Warehouse supply network shown separately from organizational tree.

## 8. Integration strategy
Provider adapter: Nexi/Nets, Worldline, Revolut; LHV bank reconciliation first, physical ECR terminal API only after provider/model validation. All adapters support capability negotiation, terminal pairing, initiate, status lookup, cancellation/refund where supported, reconciliation and sandbox tests. Hardware: secure local print bridge for LAN/USB/BT where supported, OS spool fallback, explicit shared receipt+kitchen printer. Legal receipt content and tax treatment reviewed for Estonia and merchant setup. Offline: cash sale only when local durability and reconciliation proven; card only where provider explicitly supports offline; never claim unknown payment failed.

## 9. Dependency and delivery strategy
Phase 0 (architecture): audit current PR #7,#8,#9,#12,#13,#17,#19,#20,#21,#22 and inventory PRs; define authoritative model, migrations, APIs, event contracts, ADRs; CI gates. Avoid duplicate modules.
Phase 1 (single legal entity, one site, two outlets): tenant/entity/site/outlet/register/warehouse schema, RLS, staff scopes, catalog, internal transfer ledger, simulated sale + stock depletion, real PostgreSQL integration tests.
Phase 2 (Zoo 10 outlets): full names/import, central warehouse, outlet inventory, shared menus and price overrides, kitchen routing, reporting and concurrent registers.
Phase 3 (procure-to-sell): photo/PDF delivery intake -> manager verified receipt -> inventory -> recipe/version -> production/COGS -> sales -> reconciliation -> accounting; refunds and adjustments.
Phase 4 (hardware): sandbox terminal adapters, real supported hardware test, receipt/kitchen printers incl. shared printer and outages, shift close.
Phase 5 (pilot): one small site, shadow operations, full daily reconciliation, backups/restore drill, monitoring, rollback, security and accountant acceptance; only then expand to Zoo.
Phase 6 (multi-entity): intercompany workflow, consolidated dashboards, finance isolation, legal entity-specific tax and invoice numbering.

## 10. Mandatory acceptance tests
A. Two legal entities at same site cannot read/write each other's ledger/stock; approved consolidated report does not merge ownership.
B. 10 named outlets across multiple buildings; 2 registers in same outlet; distinct and shared warehouses; role scopes and historic rename.
C. Receive 200, transfer 40, sell 12 -> exact balances and valuation, no revenue on internal transfer; duplicate transfer delivery does not double count.
D. Concurrent registers, duplicate HTTP requests, replayed outbox, delayed card success and restarted cashier cannot double charge, consume twice or post twice.
E. Recipe version change after sale does not alter historic cost; returns reverse correctly; stocktake and waste are auditable.
F. Photo extraction errors are corrected before posting; supplier invoice matching exceptions are visible.
G. Shared printer prints two distinct jobs; kitchen ticket dispatched before bill settlement, no duplicate on retries; paper-out/offline alerts.
H. Cash+card+transfer split, refunds, partial tender, terminal unknown, day close and bank reconciliation match.
I. End-to-end: supplier photo -> approved receipt -> transfer -> recipe -> order -> kitchen -> tender -> receipt -> inventory -> COGS -> journal -> shift close; failure injected at every boundary.
J. Backup restore, migrations rollback plan, security tests, device-in-loop pilot; CI must report commit SHA, exact test counts and known gaps.

## 11. Architecture decisions requiring owner input before pilot
- Actual list of legal entities and ownership; actual Zoo outlet names, buildings/zones and warehouses.
- Which sites share a central kitchen, whether stock is deducted from central or outlet stores, and when production is posted.
- Actual terminal models/providers, printer interfaces, accounting software, VAT treatment, invoice/receipt policy and fiscal/legal review.
- Whether multi-entity support is needed at launch or after one-entity pilot.

Related issues: #14 inventory, #15 payments, #16 food service, #25 hardware, #27 organization. This document defines target architecture; no claim that code or tests are complete.
