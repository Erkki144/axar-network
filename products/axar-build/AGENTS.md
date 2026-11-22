# AXAR ONE multi-agent rules — proposed (all code changes need review)

Owner: human; AION: architecture and acceptance; Claude: implementation;
Codex: independent reviewer; Gemini: UX auditor; GitHub CI: deterministic gate.

1. **Do not merge or deploy** automatically. Do not authorize bank payments,
   real terminal actions or tax filings; human must approve separately.
2. Code only within a bounded issue path; no unrelated refactors or changes
   to AXAR Network marketing application.
3. Run appropriate tests, record exact Git SHA, artifact version, limitations.
4. Financial facts (sales, VAT, settlements) must be programmatically proven;
   AI assertions are not a substitute for a test, accounting review or bank receipt.
5. No credentials, personal data or confidential supplier terms in public repo.
6. For POS UX, use >= 48 CSS pixel primary targets, keyboard access,
   role-based interfaces and clear OFFLINE / SIMULATED status.
7. Block if dependency unresolved or another reviewer finds a serious issue.
8. Every agent's PR includes traceable acceptance, rollback and risk notes.
