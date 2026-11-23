"""Dependency-aware agent task selection. Pure, stdlib-only, no AI billing.

This module *does not* execute code or trigger models; it is the safe selector
used by a future GitHub Actions integration. An issue is a task, never an
instruction with privileges higher than repository policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable, Mapping

ALLOWED_ROLES = {"claude-builder", "codex-reviewer", "gemini-ux", "human-architect"}
ALLOWED_STATES = {"backlog", "ready", "active", "review", "blocked", "done"}
SAFE_SCOPE_PREFIXES = ("products/axar-pos/", "products/axar-connect/", "products/axar-build/", "products/axar-experience/")
BLOCKED_SCOPE_PREFIXES = (".github/workflows/", ".env", "secrets/", "infrastructure/production/")
DEPENDENCY = re.compile(r"(?im)^\s*depends-on\s*:\s*(.+?)\s*$")
ISSUE_ID = re.compile(r"#([0-9]+)")


class PlanError(ValueError):
    """Invalid or unsafe planning input."""


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    role: str
    priority: int
    depends_on: tuple[str, ...]
    scope: tuple[str, ...]
    status: str
    acceptance: tuple[str, ...]


def load_tasks(raw: Mapping[str, Any]) -> tuple[Task, ...]:
    """Validate a versioned backlog, then reject missing deps or cycles."""
    if raw.get("schema_version") != 1:
        raise PlanError("unsupported backlog schema")
    tasks = []
    seen: set[str] = set()
    for entry in raw.get("tasks", []):
        if not isinstance(entry, dict):
            raise PlanError("task must be object")
        tid = entry.get("id")
        if not isinstance(tid, str) or not re.fullmatch(r"AXAR-[A-Z]+-[0-9]{3}", tid):
            raise PlanError("invalid task ID")
        if tid in seen:
            raise PlanError(f"duplicate task ID: {tid}")
        seen.add(tid)
        role = entry.get("role")
        if role not in ALLOWED_ROLES:
            raise PlanError(f"unknown role for {tid}")
        state = entry.get("status")
        if state not in ALLOWED_STATES:
            raise PlanError(f"unknown status for {tid}")
        priority = entry.get("priority")
        if type(priority) is not int or not 1 <= priority <= 5:
            raise PlanError(f"priority must be int 1..5 for {tid}")
        scope = entry.get("scope")
        if not isinstance(scope, list) or not scope or not all(isinstance(p, str) and p.startswith(SAFE_SCOPE_PREFIXES) and not p.startswith(BLOCKED_SCOPE_PREFIXES) for p in scope):
            raise PlanError(f"unsafe or empty scope for {tid}")
        acceptance = entry.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance or not all(isinstance(a, str) and a.strip() for a in acceptance):
            raise PlanError(f"missing acceptance for {tid}")
        deps = entry.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise PlanError(f"invalid dependencies for {tid}")
        tasks.append(Task(tid, str(entry.get("title", "")), role, priority, tuple(deps), tuple(scope), state, tuple(acceptance)))
    byid = {t.id: t for t in tasks}
    def visit(tid: str, visiting: set[str], done: set[str]):
        if tid in done:
            return
        if tid in visiting:
            raise PlanError(f"dependency cycle at {tid}")
        if tid not in byid:
            raise PlanError(f"unknown dependency {tid}")
        visiting.add(tid)
        for d in byid[tid].depends_on:
            visit(d, visiting, done)
        visiting.remove(tid)
        done.add(tid)
    done: set[str] = set()
    for t in tasks:
        visit(t.id, set(), done)
    return tuple(tasks)


def select_local(tasks: Iterable[Task], *, role: str | None = None) -> Task | None:
    """Only select tasks whose own state is READY and dependencies DONE.

    A review or active task blocks a conflicting scoped READY task. One task
    per dispatcher invocation, no speculative parallel finance changes.
    """
    tasks = tuple(tasks)
    byid = {t.id: t for t in tasks}
    active = [t for t in tasks if t.status in {"active", "review"}]
    eligible = []
    for t in tasks:
        if t.status != "ready" or (role is not None and t.role != role):
            continue
        if not all(byid[d].status == "done" for d in t.depends_on):
            continue
        if any(set(t.scope) & set(a.scope) for a in active):
            continue
        eligible.append(t)
    eligible.sort(key=lambda t: (t.priority, t.id))
    return eligible[0] if eligible else None


def extract_issue_dependencies(body: str) -> tuple[int, ...]:
    """Read only an anchored Depends-On: #12, #13 metadata line."""
    lines = DEPENDENCY.findall(body or "")
    if not lines:
        return ()
    out = tuple(sorted({int(n) for line in lines for n in ISSUE_ID.findall(line)}))
    if any(n <= 0 for n in out):
        raise PlanError("invalid issue dependency")
    return out


def _label_names(issue: Mapping[str, Any]) -> set[str]:
    return {item.get("name", "") if isinstance(item, dict) else str(item) for item in issue.get("labels", [])}


def select_github_issue(issues: Iterable[Mapping[str, Any]], *, owner_login: str, closed_issue_numbers: set[int]) -> int | None:
    """Choose oldest owner-authored, labelled, dependency-cleared issue.

    The caller must obtain issues with an authenticated GitHub API read.
    No user-written issue body is passed to a shell command.
    """
    eligible: list[tuple[str, int]] = []
    for i in issues:
        if "pull_request" in i or i.get("state") != "open":
            continue
        if i.get("user", {}).get("login") != owner_login:
            continue
        labels = _label_names(i)
        if "claude-ready" not in labels or "agent-in-progress" in labels or "blocked" in labels:
            continue
        number = i.get("number")
        if type(number) is not int or number < 1:
            continue
        dependencies = extract_issue_dependencies(i.get("body", ""))
        if number in dependencies or not all(n in closed_issue_numbers for n in dependencies):
            continue
        eligible.append((str(i.get("created_at", "9999")), number))
    eligible.sort()
    return eligible[0][1] if eligible else None


def release_gate(*, ci_passed: bool, independent_review: bool, human_approved: bool, auth_complete: bool, payment_integration: bool = False, terminal_certified: bool = False) -> tuple[bool, tuple[str, ...]]:
    """For demonstration and release policy: fail closed on missing evidence."""
    failures = []
    for ok, reason in ((ci_passed, "CI not green"), (independent_review, "independent review missing"), (human_approved, "human approval missing"), (auth_complete, "verified authentication missing")):
        if not ok:
            failures.append(reason)
    if payment_integration and not terminal_certified:
        failures.append("payment provider certification missing")
    return not failures, tuple(failures)


def plan_json(text: str) -> dict[str, Any]:
    tasks = load_tasks(json.loads(text))
    chosen = select_local(tasks)
    return {"schema_version": 1, "task_count": len(tasks), "eligible_task": chosen.id if chosen else None, "blockers": [t.id for t in tasks if t.status in {"blocked", "review", "active"}]}
