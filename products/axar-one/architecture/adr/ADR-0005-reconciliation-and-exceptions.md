# ADR-0005 — Reconciliation and exception queues

Status: **Proposed**.

**Decision.**
- Daily (per business day and location), the reconciliation run compares authoritative records with derived ones:
  - drawer totals ↔ sale/refund ledger (exists at close today);
  - tenders ↔ PSP settlements;
  - stock movements ↔ sales consumption;
  - POS events ↔ BOOKS exports.
- A mismatch creates a `recon.mismatch.detected` event and an exception-queue item with an owner, severity and dedup key.
- Nothing is auto-corrected. A correction is a new auditable record approved by a manager.
