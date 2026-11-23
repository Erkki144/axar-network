"""AXAR BUILD CLI. Local, deterministically testable planning; no model calls."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

from .planner import PlanError, plan_json, select_github_issue


def _get_json(url: str, token: str) -> object:
    req = Request(url, headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "User-Agent": "axar-build-planner/0.1", "X-GitHub-Api-Version": "2022-11-28"})
    with urlopen(req, timeout=15) as response:
        return json.load(response)


def github_select(repo: str, owner: str, token: str) -> int | None:
    """Read-only issue selection, supports first 100 open issues per repo.

    Stop rather than silently mis-schedule when GitHub returns >= 100 issues.
    """
    import re
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}", repo):
        raise PlanError("invalid repo slug")
    if repo.split("/")[0] != owner:
        raise PlanError("repository owner mismatch")
    issues = _get_json(f"https://api.github.com/repos/{repo}/issues?state=open&per_page=100", token)
    if not isinstance(issues, list) or len(issues) >= 100:
        raise PlanError("issue API response invalid or pagination required; refusing selection")
    # Only check dependencies referenced by eligible owner-created tasks.
    from .planner import _label_names, extract_issue_dependencies
    dependency_ids = set()
    for issue in issues:
        if issue.get("user", {}).get("login") == owner and "claude-ready" in _label_names(issue):
            dependency_ids.update(extract_issue_dependencies(issue.get("body", "")))
    closed = set()
    for number in dependency_ids:
        item = _get_json(f"https://api.github.com/repos/{repo}/issues/{number}", token)
        if isinstance(item, dict) and item.get("state") == "closed":
            closed.add(number)
    return select_github_issue(issues, owner_login=owner, closed_issue_numbers=closed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AXAR BUILD safe task selector")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("backlog", type=Path)
    select = sub.add_parser("select-github")
    select.add_argument("--repo", required=True)
    select.add_argument("--owner", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            print(json.dumps(plan_json(args.backlog.read_text(encoding="utf-8")), ensure_ascii=False))
        else:
            token = os.environ.get("GITHUB_TOKEN", "")
            if not token:
                raise PlanError("GITHUB_TOKEN missing")
            number = github_select(args.repo, args.owner, token)
            print(number if number is not None else "NONE")
        return 0
    except (PlanError, ValueError, URLError, OSError, json.JSONDecodeError) as exc:
        print(f"AXAR BUILD: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
