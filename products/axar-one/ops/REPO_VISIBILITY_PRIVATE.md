# Making `Erkki144/axar-network` private — preparation (#1, #34 S-02)

Status: **prepared, not done.** Owner agreed in principle on 2026-11-25; the switch itself still needs the owner's final confirmation. The repository is **public** today (audit E12). Changing the visibility is an owner action (Settings → General → Danger Zone → Change visibility). This page lists what changes and what to check, so the switch is safe.

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

## Impact check, 2026-11-25 (what was verified, what is still open)

| Area | Finding | Evidence | Risk |
| --- | --- | --- | --- |
| GitHub Actions | `axar-pos` (≈ 3–5 min per job, 5 jobs) and `security` (≈ 20 s) run on every PR and every push to `main`. A POS PR uses about 15–20 runner-minutes; a docs/website-only PR about 1–2 (POS jobs are skipped). | run durations on `main` after the #46/#47/#50 merges | at ~10 POS PRs per day the 2,000 free minutes last about 10–13 days. **Needs the owner's billing decision** (spending limit or paid plan) before switching. |
| Secret scanning | gitleaks job (`secret-scan`) keeps working; GitHub-native secret scanning / push protection stop without Advanced Security. | `security.yml` | low; our own scan covers PRs and `main`. |
| Vercel production | The production deployment is not rebuilt by the switch. Production is currently `dpl_5hHCHZUEbAPkDPbsrG5E4EqieATo` (commit `8de19c3`). | Vercel API | none at switch time. |
| Vercel future builds | **Open, must be tested:** with a private repo, Vercel can block a deployment whose commit author is not a member of the Vercel team. Claude's commits are authored as `Claude <noreply@anthropic.com>`. A website PR from Claude could then get a *Blocked* preview. Merge commits made on GitHub are authored by the owner and are not affected. | Vercel documentation on Git author checks for private repositories; not yet observed on this project | medium: test with one website preview right after the switch (checklist step 3). |
| Vercel preview environment | Preview deployments have **no `DATABASE_URL`**: `/api/jobs` returns 500 on previews (falls back to a local SQLite file that does not exist on Vercel). Production returns 200. Independent of visibility. | Vercel runtime log of the #51 preview | previews cannot be used to test anything that reads the database. Fix: add preview-scoped variables pointing to a **separate test database** (never the production one). |
| Vercel access to deployments | Deployment protection is "Vercel Authentication, all except custom domains"; there is no custom domain, so every deployment, production included, needs a Vercel login. Independent of visibility. | Vercel project settings | if a custom domain is added later, production pages and `/api/jobs` become public; `/api/jobs` returns customer e-mail addresses. Fix before adding a domain. |
| Claude GitHub app / sessions | keep working; the app is installed on the repository and not on public access. | — | — |
| Existing clones and history | stay as they are; treat everything already pushed as public. gitleaks found no secrets in the full history. | `secret-scan` runs | — |

### Owner decisions needed before the switch
1. GitHub Actions budget for private-repo minutes (or a spending limit).
2. Confirm that no one outside the owner needs read access (add collaborators first if they do).
3. Final "switch to private" confirmation.
