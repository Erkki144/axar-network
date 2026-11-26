"""Guard (release / merge manager): numbered artefacts must be unique.

Checks, in this branch:
- Alembic migrations: one file per revision id, a single linear chain;
- ADRs: one file per ADR number;
- architecture documents: one file per two-digit number.

Then compares the numbers this branch ADDS with the numbers every other OPEN
pull request adds (GitHub API). Two open PRs adding migration 0009, or ADR-0015,
can each pass CI alone and still break whoever merges second (seen 26.11:
#71 and #74 both add 0009; #73 and #74 both added ADR-0015).

Exit 1 = a duplicate inside this branch (always wrong).
Cross-PR collisions are reported as GitHub warnings with the PR numbers and
the merge-order fix; they do not fail the build, because which PR renumbers
depends on the merge order the owner chooses.

Usage (CI sets GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_HEAD_REF):
    python scripts/check_number_collisions.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = "products/axar-pos/axar_pos/migrations/versions/"
ADRS = "products/axar-one/architecture/adr/"
ARCH = "products/axar-one/architecture/"

PATTERNS = {
    "migration": re.compile(rf"^{re.escape(MIGRATIONS)}(\d{{4}})_[\w]+\.py$"),
    "adr": re.compile(rf"^{re.escape(ADRS)}ADR-(\d{{4}})-[\w.-]+\.md$"),
    "architecture doc": re.compile(rf"^{re.escape(ARCH)}(\d{{2}})-[\w.-]+\.md$"),
}


def classify(path: str) -> tuple[str, str] | None:
    for kind, rx in PATTERNS.items():
        m = rx.match(path)
        if m:
            return kind, m.group(1)
    return None


def local_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if classify(p)]


def duplicates(paths: list[str]) -> list[str]:
    seen: dict[tuple[str, str], str] = {}
    problems = []
    for p in sorted(paths):
        key = classify(p)
        if key in seen:
            problems.append(f"{key[0]} {key[1]}: {seen[key]} AND {p}")
        else:
            seen[key] = p
    return problems


def migration_chain() -> list[str]:
    """Every migration names an existing down_revision; exactly one head."""
    revs, downs = {}, {}
    for f in sorted((ROOT / MIGRATIONS).glob("*.py")):
        text = f.read_text(encoding="utf-8")
        r = re.search(r"^revision\s*=\s*['\"](\w+)['\"]", text, re.M)
        d = re.search(r"^down_revision\s*=\s*(None|['\"](\w+)['\"])", text, re.M)
        if not r:
            continue
        revs[r.group(1)] = f.name
        downs[r.group(1)] = d.group(2) if d and d.group(2) else None
    problems = [f"{revs[r]} points to missing down_revision {d}" for r, d in downs.items() if d and d not in revs]
    heads = set(revs) - {d for d in downs.values() if d}
    if len(heads) != 1:
        problems.append(f"migration heads: {sorted(heads)} (must be exactly one)")
    return problems


def _get(url: str, token: str | None):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "axar-collision-guard",
                                               **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def added_by_open_prs(repo: str, token: str | None, own_branch: str | None) -> dict[tuple[str, str], list[str]]:
    found: dict[tuple[str, str], list[str]] = {}
    pulls = _get(f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=100", token)
    for pr in pulls:
        if pr["head"]["ref"] == own_branch:
            continue
        page = 1
        while True:
            files = _get(f"https://api.github.com/repos/{repo}/pulls/{pr['number']}/files?per_page=100&page={page}",
                         token)
            for f in files:
                key = classify(f["filename"])
                if key and f["status"] in ("added", "renamed"):
                    found.setdefault(key, []).append(f"#{pr['number']} {f['filename']}")
            if len(files) < 100:
                break
            page += 1
    return found


def main() -> int:
    paths = local_files()
    errors = duplicates(paths) + migration_chain()
    for e in errors:
        print(f"::error::duplicate numbered artefact in this branch: {e}")

    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        try:
            others = added_by_open_prs(repo, os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_HEAD_REF"))
        except Exception as exc:  # network/API trouble must not hide the local result
            print(f"::warning::could not read open pull requests: {exc}")
            others = {}
        base = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip()
        mine = set()
        if base:
            diff = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AR", base, "HEAD"], cwd=ROOT,
                                  capture_output=True, text=True).stdout.splitlines()
            mine = {p for p in diff if classify(p)}
        for p in sorted(mine):
            key = classify(p)
            clash = [x for x in others.get(key, []) if not x.endswith(" " + p)]
            if clash:
                print(f"::warning file={p}::{key[0]} {key[1]} is also added by {', '.join(clash)}. "
                      f"Whichever merges second must renumber (migrations: next free id + down_revision).")
    print(f"checked {len(paths)} numbered files; {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
