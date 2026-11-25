# ADR-0013 — Organization hierarchy, stock ownership and the data migration contract

Status: **Proposed** (owner requirement #27; target model in owner PR #28). This ADR maps that model onto the **code that exists** and defines the migration contract that #27 asks for before any implementation.

## Target hierarchy (from #27 / #28)

```
tenant (workspace, RLS boundary)
 └─ legal_entity        registry no, VAT no, books, document series            ← NEW
 site                   physical address/campus (Tallinna Loomaaed)            ← today's `locations` row
  ├─ building/zone      optional, physical navigation only                      ← NEW (nullable)
  ├─ sales_outlet       stable code + display name; belongs to ONE legal entity ← NEW
  │   └─ register       till/device, drawer, shift, terminal, printer          ← exists, re-parented
  └─ kitchen_station    may serve several outlets                               ← NEW (today: one implicit kitchen)
 storage_location       separate graph: central, outlet store, fridge, truck, in-transit
                        references site (physical) and legal_entity (owner)    ← today's `warehouses`, extended
 outlet_stock_source    outlet × (item | category | default) → storage_location ← NEW
```

**Rules:**
1. A site is never a legal entity.
2. Each outlet sells as **exactly one** legal entity. Two entities at one site are two outlets.
3. Stock is owned by the storage location's legal entity. A sale at outlet A consumes only from A's mapped source.
4. Moving stock between storage locations of the **same** entity is a transfer: dispatch → in transit → receive (with variance). It is never revenue or VAT.
5. Moving stock to **another** entity needs an intercompany document (sale/purchase pair). It is never a silent transfer.
6. Every sale, refund, payment and stock movement stores an **immutable dimension snapshot**: legal_entity_id, site_id, building_id, outlet_id, register_id, plus the outlet code and name at that time. Renaming or moving an outlet never changes history.

## Mapping from today's schema

| Today | Becomes | Note |
| --- | --- | --- |
| `tenants` | tenant | unchanged |
| — | `legal_entities` | one default per tenant created from `tenants.name`; registry/VAT fields filled by the owner, never invented |
| `locations` (venue, has registers, tables, warehouses) | `sites` | same id, kept as the table `locations` with a view or rename in a later step, to keep existing FKs |
| — | `sales_outlets` | one default outlet per existing location (code `OUT1`, name = location name), owned by the default legal entity |
| `registers.location_id` | `registers.outlet_id` (+ `site_id` derived) | backfilled to the default outlet |
| `dining_tables.location_id` | `dining_tables.outlet_id` | backfilled |
| `warehouses(location_id, is_default)` | `storage_locations(site_id, legal_entity_id, kind)` + `outlet_stock_sources` | `is_default` becomes the outlet's default source row |
| `devices.location_id/register_id` | devices bound to **outlet** (+ register) | session scope by outlet; manager scope can be site or entity |
| `sales`, `refunds`, `payments`, `stock_movements`, `outbox_events` | + `legal_entity_id`, `site_id`, `outlet_id` (+ `outlet_code`, `outlet_name` on sales) | added NOT NULL after backfill |
| `tenant_counters(name)` (receipt, refund, ticket, invoice) | per **register** series (offline, ADR-0012) inside one legal entity | existing numbers stay; the new series starts after them, per register |
| `staff_roles(location_id)` | scope = entity / site / outlet | today's location scope maps to the site |

## Migration contract (zero data loss, reversible until cut-over)

1. **Additive migration (0006)**:
   - create `legal_entities`, `sales_outlets`, `buildings`, `kitchen_stations`, `outlet_stock_sources` and the transfer documents;
   - add nullable `outlet_id`, `legal_entity_id`, `site_id` and snapshot columns;
   - add RLS and grants to every new table.
2. **Backfill, one transaction per tenant, idempotent**:
   - one default legal entity per tenant;
   - one default outlet per location;
   - registers, tables, devices, warehouses and historic documents point at the defaults;
   - `outlet_code`/`outlet_name` on historic sales are taken from the location at backfill time and marked `snapshot_backfilled = true`.
3. **Verify** (a script, plus a test on a copy of the database): counts and sums per tenant match before and after; no NULL dimension remains; RLS tests pass.
4. **Constrain (0007)**:
   - set the columns NOT NULL;
   - add composite foreign keys that make cross-entity links impossible, e.g. `(outlet_id, legal_entity_id)` on sales must match the outlet's entity.
5. **Code switch:** services read the outlet from the device/session. The old `location_id` reads remain until every caller has moved.
6. **Downgrade:** steps 1–2 are reversible (drop the new columns and tables). Step 4 is reversible only before new outlets are created.

## Stock: transfers and in-transit (acceptance C of #28)

| Event | Central | In transit | Outlet A | Entity total |
| --- | --- | --- | --- | --- |
| receive 200 at central | 200 | 0 | 0 | 200 |
| dispatch 40 to A | 160 | 40 | 0 | 200 |
| A receives 40 | 160 | 0 | 40 | 200 |
| sell 12 at A | 160 | 0 | 28 | **188** |

- Value moves at the source's moving average (ADR-0011); a transfer never changes the total value.
- A receipt variance (e.g. 38 received) is posted as a loss movement with a reason.
- Duplicate dispatch or receive messages are idempotent through the document id.
- Outlet B is unchanged.
- **Tests:** conservation of quantity and value across every step; parallel sales at two registers of A; replayed transfer.

## Reporting

- All reports filter by entity / site / building / outlet / register / date using the **snapshot** columns.
- Consolidated reports sum per entity and exclude internal transfers from revenue.
- Stock valuation stays per owning entity.

## Owner input needed

- Actual legal entities with registry and VAT numbers.
- The real names of the zoo's outlets, buildings and warehouses. The import is owner-provided; nothing is invented.
- Whether multi-entity is needed at launch.
- Which outlets consume from central and which from their own store.
