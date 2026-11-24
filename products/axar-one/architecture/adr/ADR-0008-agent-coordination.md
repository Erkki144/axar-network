# ADR-0008 — Engineering coordination between agents

Status: **Proposed**.

**Decision.**
- GitHub is the engineering system of record.
- **Claim:** one canonical issue per task. The claim is a comment `claim: <agent> <branch>` plus a label. A claim older than 48 h without commits is considered expired.
- **Branches:** one branch per claim; draft PRs only. The dependency order is the DAG in `01-system-context.md`.
- **Review:** an independent review by the other agent before the owner gate. No self-approval and no merge or deploy without the owner.
- **Evidence:** every PR reports the exact SHA, CI links, test counts and known limits.
