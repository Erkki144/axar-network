# 09 — v1.1: offline-first, organization hierarchy, hardware — gaps and delivery order

Owner input 2026-11-25:
- #29 offline-first (P0, hard requirement);
- #27 company → site → building → outlet → register + warehouse graph;
- #25 card terminal + printers + receipt designer, with the provider research for Nexi, Worldline, LHV and Revolut and the cross-system gap list;
- #28 operating model v1.0 (owner PR, docs only);
- #16 AXAR VISION (future, not this sprint).

This page relates those requirements to the code that exists on the stack and fixes a delivery order. Design decisions are in [ADR-0012](adr/ADR-0012-offline-first-edge-node.md) (offline, supersedes ADR-0010) and [ADR-0013](adr/ADR-0013-organization-hierarchy-and-stock-ownership.md) (hierarchy and migration contract).

## What #28 v1.0 needs in v1.1

| #28 section | v1.1 delta |
| --- | --- |
| 1 Invariants | add: *a till keeps selling cash with the network or the central database down; a committed local transaction is never lost (RPO 0, as long as storage is intact); nothing is deleted on a device before the server acks it* |
| 2 Hierarchy | as is; plus a document series **per register** inside one legal entity (needed offline) |
| 3 Domains | add "Edge": device journal, sync cursor, snapshot, offline grant, bill lease, quarantine |
| 4 Events | add the device journal entry and the server ingest; the envelope gets `device_id`, `device_seq`, `hash`, `snapshot_version` |
| 8 Integration | card offline only with certified provider support (capability flag); LAN print bridge works without WAN |
| 9 Phases | **offline O1 moves into Phase 1** (before the zoo); see the order below |
| 10 Tests | add K: the #29 acceptance demo (2 outlets, 2 registers, 100+ offline sales, reboot mid-sale, reconcile exactly once) |

#28 is the owner's branch. I have not pushed to it. With the owner's approval I can open a small PR into it that applies the delta above.

## Gap audit against existing code (stack up to PR #26 @ `0622c98`)

| ID | Sev | Finding (evidence) | Fix |
| --- | --- | --- | --- |
| G-OFF-1 | **P0** | No sale without the central server: the UI calls the central API; `localStorage` is only a crash journal (`app.mjs:LS_PENDING`) | edge node (ADR-0012 §1), O1 |
| G-OFF-2 | **P0** | Document numbers come from one per-tenant counter (`services.next_number`) → two offline tills cannot both number | per-register series (ADR-0012 §3), O1 |
| G-OFF-3 | P0 | Integer primary keys on syncable rows (`drawer_sessions.id`, `sale_lines.id`, `kitchen_tickets` numbering) | ULID `public_id` on every synced row; integer PKs stay local, O1 |
| G-OFF-4 | P0 | Bills are arbitrated only by the server's version CAS (`orders._bump`) | bill leases (ADR-0012 §6), O3 |
| G-OFF-5 | P1 | Prices, VAT and staff are read live; no signed, versioned snapshot | snapshot + offline grant (§5), O2 |
| G-OFF-6 | P1 | No device heartbeat / pending-count / last-sync visibility | O4 |
| G-ORG-1 | **P0** for the zoo | No `legal_entity`, `sales_outlet`, `building`; `locations` is used as both site and outlet | ADR-0013 migration 0006/0007 |
| G-ORG-2 | P1 | `warehouses.is_default` per location; no per-outlet source mapping, no transfer / in-transit documents | `outlet_stock_sources` + transfers (ADR-0013) |
| G-ORG-3 | P1 | Sales carry `register_id` only; no entity/outlet snapshot → reports cannot survive a rename | snapshot columns (ADR-0013 rule 6) |
| G-HW-1 | P1 | Card payment is a mock inside `payments.pay_card`; no adapter interface or capability matrix | provider-neutral adapter with a capability flag (`offline_purchase`, `partial`, `refund`, `status`), mock first; Revolut + Nexi simulators first (owner research #25) |
| G-HW-2 | P1 | Receipt is plain text (`render_receipt_text`); no printer registry, print queue or kitchen print; KDS screen only | print-job queue in the same transaction (also needed offline), local print bridge, templates |

## Delivery order (proposal)

The owner asked to **stabilize the dependency graph before adding parallel features** (#25 comment). So:

0. **Stabilize.**
   - Owner decision: merge the stacked drafts in order, #19 → #20 → #21 → #22 → #23 → #24 → #26 (all green in CI), or name what blocks each one.
   - Reconcile with AION's #8 (CONNECT) on the v2 `pos.sale.completed`.
1. **O1 — offline core** (P0, #29).
2. **ORG-1 — hierarchy migration 0006/0007 with the default backfill** (#27). Small and additive, so it can run in parallel with O1, but it must land before the per-register series is finalized.
3. **O2, O3** (snapshot, leases, kitchen print queue), together with the **print-job queue** of #25.
4. **RECEIVE** (#14 photo intake) and the **transfers / in-transit** documents (#27).
5. **Terminal adapters** in sandbox (#25), then **O5 packaging** and hardware in the loop.
6. Zoo phase (10 outlets, import of the real names), then the pilot.

**Not this sprint:** AXAR VISION (#16 comment), multi-entity intercompany (Phase 6).
