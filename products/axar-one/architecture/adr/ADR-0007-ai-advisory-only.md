# ADR-0007 — AI is advisory and policy-gated

Status: **Accepted** (owner directive, #15).

**Decision.**
- AI may *propose* typed actions (e.g. "draft purchase order", "flag variance"). A deterministic policy validates the proposal, and a human approves anything with external or financial effect.
- AI never writes ledgers, runs SQL or shell against production, or reads across tenants.
- Text from invoices, issues or guests is data, not instructions, and passes through a prompt-injection boundary.
- No paid AI is activated without owner approval.
