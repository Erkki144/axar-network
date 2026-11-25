# Slice 8 — inventory and costing vertical slice (#14)

Status: **local / CI only.** Branch `claude/axar-inventory-s10-0r9ip8`, stacked on #22. This is the first runnable slice the owner asked for on #14: supplier document → stock → recipe / production → POS sale → COGS → margin → reconciliation. It is backend + API + tests. The screens (delivery-note inbox, stock card, recipe editor) are the next PR.

## Owner acceptance — passes (`tests/test_inventory.py`, SQLite and PostgreSQL)

| Step | Expected | Result |
| --- | --- | --- |
| Receive 10 kg at €5/kg net | 10 kg, €50 | ✓ |
| Recipe 0.2 kg per portion; sell 20 portions at €10 net | stock 6 kg, **€30** | ✓ |
| | COGS **€20** | ✓ |
| | net revenue **€200** | ✓ |
| | ingredient gross margin **€180** | ✓ |
| Worker runs again (redelivery) | no second consumption | ✓ |

## Also covered

| Scenario | Rule | Test |
| --- | --- | --- |
| Late invoice at €5.50/kg after 4 kg were sold | +€3 to the 6 kg in stock, +€2 COGS adjustment; quantities are not posted again; history unchanged | `test_late_invoice_price_difference_split_between_stock_and_cogs` |
| Same delivery note number again / posting twice | 409 `duplicate_document` / `already_posted` | `test_duplicate_document_and_double_post_are_refused` |
| Partial return to the supplier | at average cost; cannot return more than received; not COGS | `test_partial_return_to_supplier_at_average_cost` |
| Batch production (2 kg → 10 portions), offer sells the portion | ingredients consumed once (by production), the sale consumes portions; an offer cannot have a recipe AND an item; idempotent batch | `test_batch_production_then_sale_never_double_deducts` |
| Mixed VAT, non-recoverable VAT, freight | recoverable VAT excluded from cost, non-recoverable included; freight allocated by largest remainder (sums exactly) | `test_mixed_vat_nonrecoverable_vat_and_freight_allocation` |
| Rounding | €10.00 for 3 kg consumed 1 kg at a time → 3.33 + 3.33 + 3.34; no residue | `test_cost_rounding_never_leaves_residue` |
| Negative stock | allowed (a sale is never refused), flagged, missing part at last cost | `test_negative_stock_is_allowed_but_flagged` |
| Failed operations | nothing written | `test_failed_operations_write_nothing` |
| Sale at a location without a warehouse | consumer **blocks** (no skip, nothing consumed) | `test_sale_without_default_warehouse_blocks_consumer_instead_of_skipping` |
| Concurrency | 10 parallel sales + 3 parallel workers → exact; 10 parallel receipts + 5 parallel batches on one balance row → no lost update | `test_concurrent_sales_and_workers_consume_exactly`, `test_parallel_receipts_and_production_lose_no_update` |
| Tenancy / append-only | other tenant sees nothing; movements cannot be changed (ORM guard + PostgreSQL grants) | `test_inventory_is_tenant_scoped_and_needs_manager`, `test_stock_movements_are_append_only`, `test_postgres.py` |

**Mutation checks:**

| Mutant | Result |
| --- | --- |
| recipe AND item allowed | killed |
| invoice difference all to stock | killed |
| non-recoverable VAT dropped | killed |
| double post | killed |
| no balance lock | killed (only after adding the parallel-writers test; it survived before) |
| "consume everything = exact value" special case | **equivalent** (`v·q/q = v`), kept as documentation |

## Design

- **Exact quantities:** integers in micro base units (1 kg = 1 000 000). **Values:** integer cents, net of recoverable VAT.
- **Moving weighted average** is the accepted policy (owner decision 25 Nov, ADR-0011). Consuming q of Q costs `half_up(V·q/Q)`; consuming everything costs exactly V.
- **Append-only `stock_movements`** (receipt, supplier_return, sale_consumption, production_consumption / production_output, invoice_adjustment, cogs_adjustment). Each is unique per (kind, reference, line, item), so a sale line can never be consumed twice.
- **`stock_balances`** is the running total. It is locked `FOR UPDATE` in (item, warehouse) order, and `GET /api/v1/inventory/reconciliation` proves balance = Σ movements.
- **Item ≠ menu offer:** an offer (`products`) consumes either a versioned recipe (`stock_mode=recipe`, ingredients per portion, effective dated) or a stocked item (`stock_mode=item`, finished or resale).
- **Sales consume asynchronously** through the `stock.consume` consumer (block policy; ADR-0004). A sale never waits for or fails on stock.
- **Refunds do not restock.** Explicit disposition (restock / waste) comes with refund event v2.
- **API (manager or owner):** `/api/v1/inventory/` items, warehouses, suppliers, documents (create / post / returns), recipes, sell-item, production, stock, `items/{id}/card`, reconciliation, margin.
- **PostgreSQL:** migration `0004` with RLS on every table. The app role may only `SELECT, INSERT` on movements, document lines, recipes and batches.

## Not yet (next steps on #14)

- **Screens:** delivery-note inbox with supplier matching, receipt approval, stock card, recipe editor with live cost/yield, price calculator, drill-down receipt → sale → GL.
- **Documents and import:** purchase order, three-way match, credit notes, CSV/XML import with dry-run, lots/expiry/FEFO, stock counts, transfers, waste with reason.
- **Accounting:** GRNI accrual and GL postings (with BOOKS / AXAR CONNECT); refund disposition; period close.
- **Recipes:** nested recipes and yield/trim loss; modifiers.
- **Pending the accountant:** VAT and goods-received-not-invoiced (GRNI) treatment. The costing policy is decided (ADR-0011).
