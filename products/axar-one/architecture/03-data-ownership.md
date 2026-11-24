# 03 — Data ownership, canonical IDs and boundaries

## Boundaries

```
tenant (customer account, RLS boundary)
 └─ legal_entity (VAT registration, receipts, GL)          ← planned (G-DM-7)
     └─ location (venue; timezone, business-day cutoff)   ← timezone/cutoff planned per location (G-DM-6)
         ├─ register (till) ─ drawer_session
         ├─ device (enrolled; bound to location [+ register])
         ├─ dining_table, kitchen station (planned)
         └─ warehouse (planned #14)
```

**Rules:**
- The tenant comes from the authenticated session, never from request input. PostgreSQL RLS is the second wall.
- Every row of a tenant table carries `tenant_id`.
- Location scope comes from the device. Back-office scope comes from staff roles.

## Authoritative ownership (single writer per table)

| Data | Owner module | Writers (code) | Readers | Mutability |
| --- | --- | --- | --- | --- |
| tenants, locations, registers | IAM/admin | admin CLI, seed | all | admin-only |
| staff, staff_roles, devices, sessions, approvals | IAM | `auth.py` | API dependency | revocation, not deletion |
| auth_events | IAM | `auth.audit` | back office | **append-only** (ORM + grants) |
| products (today: item + offer + price) | MENU (planned) | seed/admin | POS, ORDERS | to be split (G-DM-1) |
| tax_rates | FINANCE rules | migration/seed | POS | versioned by date, read-only for the app |
| drawer_sessions | POS | `services.open/close_drawer`, `_claim_drawer` | POS, reports | running totals via conditional UPDATE only |
| sales, sale_lines | POS | `services.create_cash_sale` | all | **append-only** |
| refund_allocations, sale_refund_state | POS | `create_cash_refund` (CAS, row lock) | POS | counters only |
| refunds, refund_lines | POS | `create_cash_refund` | all | **append-only** |
| dining_tables | ORDERS | admin/seed | ORDERS, UI | |
| orders, order_lines | ORDERS | `orders.py` (version CAS) | UI, KDS | immutable once `paid` |
| kitchen_tickets (+lines) | KDS | `send_to_kitchen`, `mark_ready` | KDS, UI | lines append-only |
| tenant_counters | the owning module of each number series | `next_number` | — | locked increment |
| outbox_events | the producing module, same transaction | producers | relay (planned), CONNECT | **append-only** |
| payments / payment_intents | PAY (planned) | — | — | — |
| stock movements | STOCK (planned) | — | — | append-only |
| journals / exports | BOOKS (CONNECT) | CONNECT | — | staged → acknowledged |

**No cross-module writes.**
- Example of the correct pattern: `orders.pay_order_cash` calls the POS service `create_cash_sale` with a `before_commit` hook, so the sale (POS) and the order close (ORDERS) happen in one transaction through POS's own function. It does not write `sales` directly.
- Further cross-module effects go through events.

## Canonical identifiers

| Entity | Type | Notes |
| --- | --- | --- |
| tenant | string ≤64 | stable slug |
| location, register, table, drawer | integer (serial) | internal; never shown as a guessable public ID to guests |
| sale, refund, order, ticket, device, staff, session, approval, event | UUIDv4 string | public-safe within an authenticated tenant |
| receipt_no, refund_no, ticket_no | per-tenant gap-free integer | from `tenant_counters`; a legal document number |
| guest order token (planned) | ≥128-bit opaque, hashed at rest | capability, not identity (#18) |
| idempotency key | client string 8–128 | unique per (tenant, kind), bound to request hash |
| correlation_id | UUID | one per business transaction; refunds carry the original's |

Consumers must treat integer IDs as opaque and never infer order or volume from them across tenants.
