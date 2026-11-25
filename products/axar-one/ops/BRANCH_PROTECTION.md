# `main` branch protection — what to set and why (#34 S-01)

Status: **prepared, not applied.**
- Only the repository owner can change these settings.
- The Claude session has no tool for repository settings.
- Nothing below is active until the owner applies it.

## Why
`main` is not protected (audit E12). Today any push or merge reaches the branch that Vercel deploys to **production** automatically.

## Required status checks (names exactly as GitHub shows them)

| Check | Workflow | Runs on |
| --- | --- | --- |
| `secret-scan` | `security.yml` (PR #46) | every PR and every push to `main` |
| `sast-python` | `security.yml` (PR #46) | every PR and every push to `main` |
| `changes` | `axar-pos.yml` (this PR) | every PR |
| `test` | `axar-pos.yml` | every PR; **skipped** (counts as passed) when the PR does not touch the POS |
| `test-postgres` | `axar-pos.yml` | same as `test` |
| `browser-e2e` | `axar-pos.yml` | same as `test` |
| `dependency-audit` | `axar-pos.yml` | same as `test` |

### Why this PR is needed first
- **Before this PR**, `axar-pos.yml` started only when POS files changed. As a required check it would then **never report** on a website-only PR, and that PR could never be merged.
- **Now** the workflow always starts.
  - A `changes` job decides whether the POS is affected.
  - If not, the test jobs are skipped, and GitHub treats a skipped required job as passed.
  - If the diff cannot be computed, everything runs (fail-safe).

**Verified locally with the same shell logic:**

| Case | Result |
| --- | --- |
| website-only change | `pos=false` |
| POS change | `pos=true` |
| unknown base commit | `pos=true` |

The `Vercel` status is **not** made required: for an ignored build its status depends on Vercel's reporting, which has not been verified.

## Settings (GitHub → Settings → Rules → Rulesets → New branch ruleset)

| Setting | Value |
| --- | --- |
| Target | `main` (Include default branch) |
| Enforcement | Active |
| Restrict deletions | on |
| Block force pushes | on |
| Require a pull request before merging | on |
| Required approvals | **0** (see note) |
| Require status checks to pass | on, with the 7 checks above |
| Require branches to be up to date before merging | on |
| Bypass list | Repository admin, **only "for pull requests"** (emergency) |

**Note on approvals.** All PRs here, including those written by Claude and AION, are opened under the owner's account `Erkki144`. GitHub does not let an author approve their own PR. With 1 required approval, no PR could be merged without a bypass.

Recommendation:
- set 0 required approvals and keep the owner's explicit "merge" decision as the human gate;
- or add a second human GitHub account as reviewer, then set approvals to 1.

### Ruleset JSON (Rulesets → Import a ruleset)

```json
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0, "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false, "require_last_push_approval": false,
        "required_review_thread_resolution": true } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "secret-scan" }, { "context": "sast-python" }, { "context": "changes" },
          { "context": "test" }, { "context": "test-postgres" },
          { "context": "browser-e2e" }, { "context": "dependency-audit" } ] } }
  ],
  "bypass_actors": [ { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request" } ]
}
```

(`actor_id: 5` is GitHub's built-in *Repository admin* role.)

## Order
1. Merge #46 (`security.yml`), then #47, then **this PR**, so that all 7 checks exist on `main`.
2. Apply the ruleset.
3. Open a trivial test PR and confirm that the 7 checks appear and report.
4. Note: the checks in the ruleset must have run at least once on `main` or on a PR to be selectable in the UI. Import by JSON works regardless.
