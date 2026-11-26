# 12 — Register map and change journeys ("the nervous system")

Status: **Proposed**. Extends `03-data-ownership.md` (single writer per table) and `04-event-catalog.md` (events).
Decision behind it: `adr/ADR-0016-master-data-as-versioned-registers.md`.

## Kokkuvõte eesti keeles

Iga andmekogum on **register**, millel on üks omanik-moodul. Ainult omanik kirjutab registrisse.
Iga muudatus tekitab **sündmuse**, mis salvestatakse samas tehingus. Sündmuse järgi uuenevad kõik seotud registrid,
igaüks täpselt üks kord. **Kooskõlareegel** kontrollib, et registrid klapivad. Iga kirje teab oma **päritolu**,
nii et teekonda saab jälgida mõlemas suunas.

Registrite liigid:
- **Põhiandmed** (kuidas asjad on): versioonitud ja kehtivuskuupäevaga. Vana versioon jääb alles.
- **Tehingud** (mis juhtus): ainult lisatakse. Parandus on vastupidine dokument.
- **Tuletatud vaated** (aruanded, laoseis, KDS): arvutatakse tehingutest ja saab alati uuesti ehitada.

**Suurim puudus täna:** põhiandmete muutused (toode, hind, retsept, soodustus, töötaja, ladu) ei tekita sündmusi.
Need on andmebaasis otse muudetud väärtused. Seda parandavad ADR-0016 ja tegevuskava sammud P1-1, P1-5 ja P1-8.

Märgid tabelites: ✅ olemas koodis ja testitud · ◐ osaliselt · ☐ puudu (planeeritud).

---

## 1. Register map

### 1.1 Master data (versioned, effective-dated — ADR-0016)

| Register | Owner module | Key fields (meaning) | Referenced by | Change event | State |
|---|---|---|---|---|---|
| **Organisation** tenant → legal entity → location → register / warehouse / device | IAM / ORG | legal entity = VAT + receipts + GL; location = timezone + **business-day start**; register = receipt series | everything | `org.unit.changed` | ◐ (no legal entity, C26) |
| **Product** (item) | CATALOG | name (i18n), group, type (goods / dish / service / gift card / system), base unit, VAT class, stock mode (none / item / recipe), active, hidden at till | price, recipe, POS layout, stock, sale lines | `catalog.product.changed` | ✅ P1-1a |
| **Product group** (tree) | CATALOG | parent, sort, default VAT / printer / station | product, reports, discounts | `catalog.group.changed` | ✅ P1-1a |
| **Price list → price version** | PRICING | list scope (outlet, order type), price incl./excl. VAT, **valid from**, valid to | till, sale line snapshot, margin | `pricing.price.published` | ✅ P1-1a (four eyes, R14) |
| **Recipe → recipe version** | RECIPES | components (item / sub-recipe), qty, loss %, from warehouse, per order type, **valid from** | stock consumption, cost, margin | `recipe.version.published` | ✅ versioned (C14), no event |
| **Discount rule** | PRICING | manual / auto, %, amount, validity window, outlets, groups, combinable, role limit, customer-only | till, sale line, reports | `pricing.discount.changed` | ☐ |
| **Payment method** | PAYMENTS | type (cash / card / transfer / voucher / room), tip allowed, archived | tender, drawer, books | `payments.method.changed` | ◐ (hard-coded) |
| **Order type** | ORDERS | dine-in / takeaway / delivery → price list, service charge, tax code | order, price lookup, reports | `orders.type.changed` | ☐ |
| **POS layout** | CATALOG | register → panels → button grid (product / payment / discount / function) | till | `catalog.layout.published` | ☐ (seed) |
| **Supplier** | INVENTORY | reg. code, terms, IBAN, supplier item code + unit factor + price | purchase docs, reorder | `inventory.supplier.changed` | ✅ (no event) |
| **Warehouse** | INVENTORY | location, outlet ↔ default warehouse | stock, consumption | `inventory.warehouse.changed` | ✅ (no event) |
| **Customer** | CRM | type (private / business), card no., tier, terms, consents | sale, invoice, discount, loyalty | `crm.customer.changed` | ◐ (name on invoice only) |
| **Staff + permission group** | IAM | person, groups, locations, PIN / card | every command (authorisation) | `iam.staff.changed`, `iam.group.changed` | ◐ (5 fixed roles, C10) |
| **Settings** | owning module | business-day start, rounding, decimals, numbering, offline limit | the owning module | `<module>.settings.changed` | ◐ (offline limit ✅) |
| **Tax rates** | FINANCE | rate by **valid from** | price, sale line | read-only (migration) | ✅ |

### 1.2 Transactions (append-only; corrections are new documents)

| Register | Owner | Created by | Event | Reconciled by | State |
|---|---|---|---|---|---|
| Sale + lines (snapshot of product, price version, VAT, discount) | POS | till / office invoice | `pos.sale.completed` | R2, R3, R5, R6 | ✅ |
| Refund / credit + lines (+ stock disposition) | POS | till with approval | `pos.refund.completed` | R4, R5, R6 | ◐ (cash only; disposition #48) |
| Tender / payment | PAYMENTS | till, terminal, bank import | `payment.*` | R3, R12 | ◐ (simulated) |
| Drawer session | POS | till open / close | `drawer.opened`, `drawer.closed` | R1 | ✅ |
| Drawer cash movement (safe drop, float top-up, deposit) | POS | till / office, manager step-up for OUT | `drawer.cash.moved` | R1 (running in/out = ledger; closing expected includes them) | ✅ #68 (online tills only; not in books export yet → P1-3; offline tills → P2-1) |
| Order + kitchen ticket | ORDERS / KDS | till, guest | `order.*`, `kitchen.ticket.*` | R11 | ✅ |
| Stock movement (receipt, consumption, production, write-off, count, transfer) | INVENTORY | office doc, sale consumer | `stock.changed` → `stock.movement.posted` | R9, R10 | ◐ (no count / write-off / transfer) |
| Purchase document (delivery note / invoice / order / credit) | INVENTORY | office, EDI | `inventory.document.accepted` | R10 | ◐ (no event name yet) |
| Receivable (B2B invoice) + bank match | PAYMENTS | office, bank import | `receivable.*` | R12 | ◐ (API only) |
| Accounting export row | BOOKS | consumer `books.export` | `books.row.acknowledged` | R8 | ◐ (no delivery, C16) |
| Device journal entry | SYNC | offline till | `sync.*` | R13 | ✅ |
| Audit log | IAM | every command | — | append-only grants | ✅ |

### 1.3 Derived views (rebuildable, never the source of truth)
Stock on hand (as of any time), margin, daily / summary / shift reports, menu-engineering report, KDS board, guest status, till price cache,
customer tier, reorder proposals, AI trends (ADR-0007: advisory only).

---

## 2. Change journeys

Format: **trigger → same transaction (atomic) → asynchronous reactions (each exactly once, via inbox) → check**.

### J1 Sale (✅ core, ◐ tail)
1. Till: `create_cash_sale` / bill payment → **atomic:** sale + lines (price, VAT, discount **snapshots**), tender, drawer totals, receipt number, `pos.sale.completed`.
2. Reactions: `stock.consume` (recipe version valid at sale time → stock movements) ✅ · `books.export` (export queue) ✅ · loyalty points ☐ · reorder proposal ☐ · KDS / SSE ✅.
3. Check: R1–R3, R5, R6, R8, R9, R10.

### J2 Price change (✅ P1-1a online tills; offline tills ☐ → P1-1d)
1. Office: new price version with **valid from** → impact preview (outlets, order types, margin change) → publish → **atomic:** price version + `pricing.price.published`.
2. Reactions: till price cache (online immediately; offline tills on next contact, then sale lines carry the version they used) · menu / TV (#40) · margin alert.
3. History: past sales keep their snapshot. **Never** recomputed.
4. Check (new rule): every sale line's price equals a published version valid at sale time for that outlet / order type, or carries an approved manual price.

### J3 Recipe change (◐ → P1-1)
1. Office / chef: new recipe version → cost preview (food-cost %, margin) → publish → `recipe.version.published`.
2. Reactions: future sales consume by the new version · cost & margin recomputed for **future** periods · alert if food-cost % exceeds the threshold.
3. Check: R9 uses the version valid at sale time.

### J4 Product deactivation / rename (☐ → P1-1)
- Rename: references are by ID, so nothing breaks; receipts keep the snapshot name.
- Deactivate: removed from till layout and price lists going forward; **blocked** if open orders or open stock documents reference it (shown in preview).
- Delete: only if never referenced; otherwise deactivate.

### J5 Stock count (☐ → P1-7)
1. Office: count document (draft) → counted quantities → close with permission "close stock count" → **atomic:** difference movements + `stock.movement.posted` per line.
2. Reactions: stock value · books (inventory adjustment) · shrinkage report.
3. Check: R10 + new rule "count closed once; differences = counted − book at close time".

### J6 Purchase receipt (✅ core)
Office / EDI: delivery note → accept → stock movements + moving-average cost (ADR-0011) → supplier last price → books. Check R10.

### J7 Cancellation / credit (☐ → P1-2, #48)
Approval → reversing documents in **every register the original touched**, found through the trace (`/admin/trace/sales/{id}`):
tender reversal, drawer, stock (restock sealed goods / write off prepared food / no movement), books, loyalty. Check R1–R13.

### J8 Staff / permission change (◐ → P1-8)
Office: change group or permission → `iam.group.changed` → tills receive it on next contact → effective for the **next** command; audit log.
Revocation (lost card, leaver) is immediate online and blocks the PIN on next contact offline.

### J9 Offline till returns (✅)
Journal entries replay exactly once in order; anything that conflicts with current master data (e.g. deactivated product) is **accepted as a fact**
(the sale happened) and flagged for review, never silently dropped. Check R13.

### J10 Period close (☐ → with P1-3)
Accountant closes a month → `books.period.closed` → no posting may be dated inside it; corrections go into the open period with a reference.

---

## 3. Invariants every journey obeys

1. **Single writer** per register (`03`). Others react to events.
2. **Same transaction:** state change + event. Never one without the other (ADR-0004).
3. **Exactly-once effect:** consumers record `(consumer, event_id)` with their effect.
4. **Snapshots on transactions:** a transaction copies the master-data version it used (price, VAT, recipe, name).
5. **No overwrite, no delete** of anything a transaction referenced. Versions and reversals instead.
6. **Traceable both ways:** `correlation_id` and `causation_id` on every event and derived row.
7. **Every new register gets a reconciliation rule and a tamper test** (DELIVERY_ROADMAP §3.4).
8. **Impact preview before publishing** master-data changes that reach tills or money.
