# ADR-0010 — Offline policy

Status: **Superseded by [ADR-0012](ADR-0012-offline-first-edge-node.md)** (owner directive #29, 2026-11-25: offline-first is P0). Kept for history; it describes the behaviour of the code up to PR #26.

**Decision for v1:**
- No money operation completes without a server answer. The UI shows "unknown", keeps the journal and retries with the same key (built).
- A future offline mode needs its own ADR and must have:
  - a signed short-lived offline grant;
  - cash sales only, with a per-device cap;
  - no refunds or drawer close;
  - local numbering from a reserved block;
  - reconciliation on reconnect.
- Card payments offline are out of scope.
