# `main` branch protection — exact settings for the owner (#34 S-01)

Status: **ready to apply, not applied.** Only the repository owner can change
these settings; the Claude session has no tool for repository settings.

Owner decision 2026-11-25: branch protection on `main` is **mandatory**.

## Preconditions (met on 2026-11-25)
- PRs #46, #47 and #50 are merged, so all 7 checks below exist on `main` and
  run on every PR.
- The checks have run on `main` (push runs after each merge were green), so
  GitHub can select them by name.

## Required status checks (names exactly as GitHub shows them)

| Check | Workflow | Runs on |
| --- | --- | --- |
| `secret-scan` | `security.yml` | every PR and every push to `main` |
| `sast-python` | `security.yml` | every PR and every push to `main` |
| `changes` | `axar-pos.yml` | every PR |
| `test` | `axar-pos.yml` | every PR; **skipped** (counts as passed) when the PR does not touch the POS |
| `test-postgres` | `axar-pos.yml` | same as `test` |
| `browser-e2e` | `axar-pos.yml` | same as `test` |
| `dependency-audit` | `axar-pos.yml` | same as `test` |

Not required: the Vercel status. For an ignored build Vercel reports the
deployment as canceled; making it required would block every POS-only PR.

## Click path (GitHub → repository → Settings → Rules → Rulesets → New ruleset → New branch ruleset)

| Field | Value |
| --- | --- |
| Ruleset name | `main-protection` |
| Enforcement status | **Active** |
| Bypass list | **empty** (see "Bypass" below) |
| Target branches | Add target → **Include default branch** |
| Restrict creations | off |
| Restrict updates | off |
| Restrict deletions | **on** |
| Require linear history | off (PRs are merged with merge commits) |
| Require deployments to succeed | off |
| Require signed commits | off |
| Require a pull request before merging | **on** |
| → Required approvals | **0** (see "Approvals") |
| → Dismiss stale pull request approvals when new commits are pushed | on |
| → Require review from Code Owners | off |
| → Require approval of the most recent reviewable push | off |
| → Require conversation resolution before merging | **on** |
| → Allowed merge methods | Merge (squash/rebase may stay enabled) |
| Require status checks to pass | **on** |
| → Require branches to be up to date before merging | **on** |
| → Status checks | the 7 checks above (source: GitHub Actions) |
| Block force pushes | **on** |
| Require code scanning results | off (needs GitHub Advanced Security on a private repo) |

## Same settings as JSON (Rulesets → New ruleset → Import a ruleset)

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
  "bypass_actors": []
}
```

## Approvals
All PRs here, including those written by Claude and AION, are opened under the
owner's account `Erkki144`. GitHub does not let an author approve their own
PR, so "1 required approval" would block every merge unless someone bypasses
it. With **0 approvals**, the human gate stays the owner's explicit "merge"
decision in the conversation, and the checks are enforced by GitHub.
If a second human GitHub account joins as reviewer, raise approvals to 1.

## Bypass
Recommended: **no bypass actors.** The Claude session acts with the owner's
GitHub token, which has the admin role. With "Repository admin may bypass",
that session could merge past failing checks; with an empty bypass list it
cannot. For a real emergency the owner can set the ruleset to *Disabled*
for a few minutes (Settings → Rules → Rulesets → `main-protection`) and turn it
back on; GitHub records that in the ruleset history.

## After applying — verification (Claude can do this, no settings needed)
1. Open a trivial docs-only PR: the 7 checks must appear; the 4 test jobs
   show as skipped and the PR is mergeable.
2. Open a PR with a deliberately failing test (closed afterwards, never
   merged): the merge button must be blocked.
3. Settings → Rules → Rulesets shows `main-protection` as *Active* with
   target "default branch". (A test push straight to `main` is deliberately
   not part of the check: if the rule were not active, it would change `main`.)
