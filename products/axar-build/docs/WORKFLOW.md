# AXAR ONE unified work protocol v1

## Truth and scope

GitHub is the durable source of truth. The PR diff, status checks, test artifacts and issue transitions are authoritative; AI prose and claimed test counts never substitute for CI logs. All agents must record exact SHA and test command. AION is not a persistent service: the human starts chat-based review unless a separately activated Automation or GitHub event triggers a runnable agent.

## Lifecycle

DISCOVER → SPECIFY → READY → CLAIMED → IMPLEMENTING → CI → CODEX REVIEW → GEMINI UX REVIEW (if relevant) → HUMAN ACCEPTANCE → STAGING → RELEASE.

For non-UI changes, Gemini review is optional but must be recorded as not applicable. For financial core changes, independent engineering and finance review are mandatory. For a documentation-only change, automatic checks can be simplified but cannot bypass the human merge gate.

## Core contracts

- One issue one scope, one single responsible implementer.
- Before coding, list acceptance cases (normal, partial failure, offline, concurrency, accessibility, taxes).
- Before marking READY: prerequisites closed, file scope known, acceptance criteria and rollback attached, source links dated.
- `claude-ready` labels request one bounded coding run; max 12 turns/workflow and budget configured externally.
- Agent opens draft PR, never merges or deploys; CI checks must be a branch protection requirement.
- Codex reviews source changes independently, including tests and cross-tenant permissions; it may suggest but cannot self-certify production.
- Gemini audits real cashier workflows, roles, localization, touch sizes and edge cases; designs are hypotheses until measured with actual users.
- ChatGPT/AION can comment using connected GitHub tools when the user asks, but will not magically watch updates without an enabled recurring/event automation.

## Operational hygiene

Never pass secrets in issue bodies, comments, prompts, build logs or code. Never enable `pull_request_target` to execute arbitrary contributor code. Limit agent trigger authors to repository owners or configured trusted collaborators, preferably in a private repository. Pin third-party actions to reviewed immutable SHAs before production adoption. Never run user-written arbitrary shell via issue body or PR title. Keep `GITHUB_TOKEN` permissions narrow.

## Release hard gate

1. CI green on exact head SHA and migrations/integration tests reproducible.
2. Independent engineer accepts any financial, auth or hardware change.
3. Owner explicit release approval.
4. Terminal provider authorization/certification for real card processing; audit of fiscal obligations.
5. Backup/restore test and roll-back plan.
6. Change log, monitoring and staged rollout.

No release without all applicable evidence; automation can stop the process, not waive gates.

## Product UX metrics (TARGETS, not claims)

- Express 3-item cash transaction: target under 12 seconds excluding tender handling, median with trained cashier.
- Every basic interaction: target <= item selections + 2 navigation actions to payment.
- 100% of critical money actions show both amount and payment type.
- Primary tap target >= 48 CSS px; minimum accessibility conformant target and keyboard labels.
- Restore interrupted cart within 3 seconds; network/power-loss test required before real launch.
- Pilot with at least 20 new operators across express, restaurant and retail; collect task time, mistakes and subjective workload (NASA-TLX) before claiming best usability.

## Open system dependencies

- Real Anthropic account/API or supported auth connected to GitHub Actions.
- OpenAI Codex cloud code review/GitHub Action enabled and authenticated.
- Gemini CLI action authenticated if activated.
- Protected private product repo; default-branch workflow review; pilot terminal + printer + tax review.
