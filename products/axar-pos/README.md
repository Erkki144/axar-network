# AXAR POS — isolated product bootstrap

Status: **Sprint 0 / local demo / not production ready**.

AXAR POS is a separate future product from the existing AXAR Network content-marketing agents. This branch preserves `main` and starts product architecture and engineering handoff.

## Scope of first running prototype

- Estonian touchscreen cash-sale interface
- FastAPI + SQLAlchemy cash checkout; server-owned price and VAT calculations
- Persistent receipt record, idempotency check, UTC daily summary preview
- API and financial tests
- Agent roles for Claude Code, Codex and Gemini

The tested local Sprint 0 source is packaged separately as `axar-pos-sprint0.zip` in the ChatGPT delivery. **The runtime source is not yet merged into this GitHub branch.** Do not infer deployability from this planning commit.

## Immediate next engineering milestone

1. Publish the tested Sprint 0 source into a dedicated private `axar-pos` repository (preferred) or a self-contained `products/axar-pos` folder.
2. Configure CI, mandatory PR checks, reviewer ownership and staged secrets.
3. Run full tests, scan dependencies, validate accounting and money invariants.
4. Add identity, authorization and proper production migrations before external access.

See `docs/ARCHITECTURE.md` and `docs/BUILD_HANDOFF.md`.
