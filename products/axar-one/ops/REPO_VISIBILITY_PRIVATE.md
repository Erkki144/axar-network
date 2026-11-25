# Making `Erkki144/axar-network` private — preparation (#1, #34 S-02)

Status: **prepared, not done.** The repository is **public** today (audit E12). Changing the visibility is an owner action (Settings → General → Danger Zone → Change visibility). This page lists what changes and what to check, so the switch is safe.

## What gets better
- The source code, architecture, threat model (`10-threat-model-sec0.md`) and issue discussions are no longer readable by everyone.
- The repository can no longer be forked publicly. **Existing public forks stay public.** Check with: Insights → Forks.

## What is lost or changes — check each before switching

| Area | Effect when private | Action |
| --- | --- | --- |
| GitHub Actions | private repos use the account's free minutes (GitHub Free: 2,000 min/month for private repos; public repos are free). Our CI uses about 10–15 runner-minutes per POS PR | watch usage in Settings → Billing; caching is already on |
| Secret scanning / push protection (GitHub native) | free for public repos; for private repos it needs GitHub Advanced Security (a paid plan) | our own `secret-scan` job (gitleaks, PR #46) keeps working |
| Dependabot alerts | still available for private repos | enable in Settings → Code security |
| Vercel | the Vercel GitHub app keeps access after the switch; deployments continue | open one preview after the switch to confirm |
| Claude GitHub app / Claude Code sessions | keep working (the app is installed on the repo) | — |
| AION / external AI reviewers (PR #9 workflows) | unchanged; they post as the owner's account | — |
| Public links (README, docs shared outside) | return 404 to anyone without access | tell anyone using them |
| Stars / watchers | stars from outsiders are removed by GitHub | — |
| Existing public forks and clones | **stay public** — git history already published is not retracted | treat everything committed so far as public (no secrets were found in history: audit E9 + gitleaks) |

## Checklist on switch day
1. Confirm that no one outside the owner needs read access, or add them as collaborators.
2. Switch the visibility.
3. Open a test PR: CI runs, Vercel builds a preview, and the Claude session still has access.
4. Enable Dependabot alerts.
5. Apply the branch protection ruleset ([BRANCH_PROTECTION.md](BRANCH_PROTECTION.md)).
