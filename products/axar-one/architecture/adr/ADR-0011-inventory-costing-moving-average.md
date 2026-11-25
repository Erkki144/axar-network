# ADR-0011 — Inventory costing: moving weighted average

Status: **Accepted** (owner decision, 25 Nov 2026). Implemented in #23.

**Decision.**
- Stock is valued at the moving weighted average per (item, warehouse), in integer cents, net of recoverable VAT.
- A receipt adds its cost; non-recoverable VAT and allocated freight are included.
- Consuming `q` of `Q` units on hand with value `V` costs `half_up(V·q/Q)`, and consuming everything costs exactly `V`, so no rounding residue stays on an empty balance.
- A late invoice price difference is split between the received quantity still on hand (adjusts stock value) and the part already consumed (COGS adjustment). Past movements are never rewritten.
- Negative stock is allowed so that sales are never refused. The missing quantity is costed at the last known average and flagged for reconciliation.

**Consequences.**
- Historic cost snapshots come from the append-only `stock_movements`.
- Switching to another policy (e.g. FIFO layers) later needs a new ADR and a dated migration; it is never applied retroactively.
- Still open for the accountant: VAT and GRNI (goods received, not invoiced) accounting treatment.
