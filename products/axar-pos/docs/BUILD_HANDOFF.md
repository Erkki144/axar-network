# AXAR BUILD — agent task orchestration

Human product owner: goals and risk boundaries. AION: architectural guidance and acceptance criteria. Claude Code: feature implementation. Codex: independent review, adversarial tests and fixes. Gemini: UX, accessibility and external documentation review. GitHub CI: objective test gate. A qualified human engineer signs off security/financial production releases.

**Agent connections and continuous runners are not installed by this commit.** These are implementable responsibilities, not claims of autonomous agents already operating.

## Required issue fields

Goal, non-goals, endpoints/data contract, acceptance tests, failure modes, security risks, implementation agent, reviewer agent, rollback plan.

## State transitions

`BACKLOG -> READY -> IMPLEMENTING -> CI_CHECK -> INDEPENDENT_REVIEW -> HUMAN_APPROVAL -> STAGING -> RELEASED`

Failed checks return work to IMPLEMENTING with reproducible evidence.

## Safety

- No unattended production deployments, bank transfers or tax submissions.
- No agent access to production bank secrets or customer PII in a coding sandbox.
- Budget, token, sandbox scope, change scope, timeouts and retries per task.
- Human review required for payment state transitions, accounting ledger, secrets and data migrations.
- Do not auto-merge a bot's code based on its own approval.
