# AXAR BUILD 0.1 — shared work queue for AION, Claude, Codex and Gemini

**Created 2026-11-22.** Engineering prototype, not a continuously running service and not a production deployment. GitHub Issues + protected pull requests are the system of record. This package adds a tested deterministic planner, security gates, architecture policy and an opt-in GitHub Actions blueprint for the missing agent runtime.

## Why one workflow?

- AION: product architecture, acceptance tests and independent code review in GitHub.
- Claude Code: implement code on branches, never merge its own PR.
- Codex: independent code/security review via official Codex GitHub Action (optional API key).
- Gemini: independent interaction design/UX review via Gemini CLI GitHub Action (later, after authentication).
- GitHub: CI, mandatory reviews, status checks, run logs, protected main.
- Human: owner approves commercial release, bank/payment credentials and production deployment.

The ChatGPT conversation **is not a persistent worker**. The `on: schedule` GitHub Action runs only after workflow is merged into the default branch, enabled and its secrets are configured. Until then, agent execution must be started from Claude Code or an authorized workflow dispatch.

## CLI (stdlib only)

```bash
cd products/axar-build
python -m axar_build.cli plan backlog.json
python -m pytest -q
```

Run `python -m axar_build.cli select-github --repo Erkki144/axar-network --owner Erkki144` to read one eligible issue, requiring `GITHUB_TOKEN` read-only in the environment. **Never paste tokens in chat.**

An issue must be open, authored by the repository owner, labelled `claude-ready`, not marked `blocked`/`agent-in-progress`, and all `Depends-On: #N` issues must be closed. Only one issue per invocation. The CLI does not post, modify labels, invoke models, run shell from issue text or merge code.

## One-time activation (not done)

1. Move AXAR commercial code to a PRIVATE repo and turn on protected branches with PR reviews and required status checks.
2. Copy workflow(s) to that repo's **default branch** via reviewed PR. Schedule/event triggers don't run from this unmerged branch.
3. Configure `ANTHROPIC_API_KEY` (or supported OAuth/federation), `OPENAI_API_KEY` and `GEMINI_API_KEY` as GitHub Actions secrets for whichever agents you want. Explicitly set `AXAR_AGENT_RUNS=enabled` and/or `AXAR_AI_REVIEWS=enabled` under Actions repository variables. Both are **disabled by default** to avoid unexpected model costs.
4. Confirm minimum GitHub Actions permissions, model budgets, and notification routing. Existing Claude Code Cloud watching PRs is independent from GitHub Actions paid API jobs; do not assume Claude Cloud subscriptions pay for API action usage.
5. Add `claude-ready` only to a dependency-cleared, owner-authored issue. CI is first; do not enable automatic merge or deploy.

## Stop conditions

- Key missing -> no paid agent run.
- Unsafe scope -> task fails validation.
- Same PR has failing CI -> no release.
- Missing independent reviewer or human owner approval -> no merge/deploy.
- Sensitive payment, customer identity, real bank actions -> human approval and specialized audit.

See `docs/WORKFLOW.md` and backlog.json. See `../axar-experience/research` for the POS experience brief.
