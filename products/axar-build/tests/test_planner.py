import json
import pytest
from axar_build.planner import (
    PlanError, load_tasks, select_local, extract_issue_dependencies,
    select_github_issue, release_gate, plan_json,
)


def t(id, status="ready", deps=None, role="claude-builder", scope=None, priority=1):
    return {"id": id, "title": "Task", "role": role, "scope": scope or ["products/axar-pos/core/"], "priority": priority, "depends_on": deps or [], "status": status, "acceptance": ["passing tests"]}


def backlog(items):
    return {"schema_version": 1, "tasks": items}


def test_no_ready_until_dependency_done():
    items = [t("AXAR-POS-001", status="review"), t("AXAR-POS-002", deps=["AXAR-POS-001"], scope=["products/axar-experience/"])]
    assert select_local(load_tasks(backlog(items))) is None
    items[0]["status"] = "done"
    assert select_local(load_tasks(backlog(items))).id == "AXAR-POS-002"


def test_priority_then_id_sort():
    items = [t("AXAR-UX-001", scope=["products/axar-experience/a/"], priority=3), t("AXAR-POS-002", scope=["products/axar-pos/b/"], priority=2)]
    assert select_local(load_tasks(backlog(items))).id == "AXAR-POS-002"


def test_active_scope_blocks_conflicting_task():
    items = [t("AXAR-POS-001", status="active"), t("AXAR-POS-002")]
    assert select_local(load_tasks(backlog(items))) is None


def test_distinct_scope_can_run():
    items = [t("AXAR-POS-001", status="review"), t("AXAR-UX-001", scope=["products/axar-experience/"])]
    assert select_local(load_tasks(backlog(items))).id == "AXAR-UX-001"


def test_rejects_unknown_dependency():
    with pytest.raises(PlanError, match="unknown dependency"):
        load_tasks(backlog([t("AXAR-POS-001", deps=["AXAR-POS-009"])]))


def test_rejects_cycle():
    with pytest.raises(PlanError, match="cycle"):
        load_tasks(backlog([t("AXAR-POS-001", deps=["AXAR-POS-002"]), t("AXAR-POS-002", deps=["AXAR-POS-001"])]))


@pytest.mark.parametrize("broken", [
    {"role": "all-powerful"}, {"priority": "1"}, {"status": "unknown"},
    {"scope": [".github/workflows/"]}, {"scope": ["infrastructure/production/"]},
    {"scope": ["products/axar-pos/", ".env"]}, {"acceptance": []},
])
def test_rejects_invalid_task(broken):
    item = t("AXAR-POS-001")
    item.update(broken)
    with pytest.raises(PlanError):
        load_tasks(backlog([item]))


def test_rejects_duplicate_ids():
    with pytest.raises(PlanError, match="duplicate"):
        load_tasks(backlog([t("AXAR-POS-001"), t("AXAR-POS-001")]))


def test_rejects_unknown_schema():
    with pytest.raises(PlanError):
        load_tasks({"schema_version": 8, "tasks": []})


def test_plan_json():
    out = plan_json(json.dumps(backlog([t("AXAR-POS-001")])))
    assert out["eligible_task"] == "AXAR-POS-001" and out["task_count"] == 1


def test_issue_dependencies_parse_only_anchored_line():
    assert extract_issue_dependencies("Hi #5\nDepends-On: #2, #17\nnotes #999") == (2, 17)
    assert extract_issue_dependencies("Depends-On: none") == ()


def issue(number, *, login="Erkki144", labels=None, body="", state="open", created="2026-11-22T00:00:00Z"):
    return {"number": number, "user": {"login": login}, "labels": [{"name": n} for n in (labels or [])], "body": body, "state": state, "created_at": created}


def test_issues_owner_only_and_oldest():
    issues = [issue(3, labels=["claude-ready"], created="2026-11-22T02:00:00Z"),issue(1, labels=["claude-ready"], created="2026-11-22T01:00:00Z"), issue(2, login="stranger", labels=["claude-ready"], created="2026-11-22T00:00:00Z")]
    assert select_github_issue(issues, owner_login="Erkki144", closed_issue_numbers=set()) == 1


def test_dependency_requires_closed_issue():
    issues = [issue(3, labels=["claude-ready"], body="Depends-On: #2")]
    assert select_github_issue(issues, owner_login="Erkki144", closed_issue_numbers=set()) is None
    assert select_github_issue(issues, owner_login="Erkki144", closed_issue_numbers={2}) == 3


def test_issue_rejects_in_progress_and_blocked_and_pr():
    x = issue(2, labels=["claude-ready", "agent-in-progress"])
    y = issue(3, labels=["claude-ready", "blocked"])
    z = issue(4, labels=["claude-ready"])
    z["pull_request"] = {}
    assert select_github_issue([x,y,z], owner_login="Erkki144", closed_issue_numbers=set()) is None


def test_release_gate_fails_closed():
    success, reasons = release_gate(ci_passed=True, independent_review=True, human_approved=False, auth_complete=True)
    assert success is False and "human approval missing" in reasons
    success, reasons = release_gate(ci_passed=True, independent_review=True, human_approved=True, auth_complete=True, payment_integration=True, terminal_certified=False)
    assert success is False and "payment provider certification missing" in reasons
    assert release_gate(ci_passed=True, independent_review=True, human_approved=True, auth_complete=True) == (True, ())


def test_github_select_picks_owner_issue_not_trust_rogue_issues(monkeypatch):
    from axar_build import cli
    calls = []
    def fake_get(url, token):
        calls.append(url)
        return [issue(4, login="attacker", labels=["claude-ready"]), issue(5, labels=["claude-ready"], body="Depends-On: #2") ] if url.endswith("?state=open&per_page=100") else {"state": "closed"}
    monkeypatch.setattr(cli, "_get_json", fake_get)
    assert cli.github_select("Erkki144/axar-network", "Erkki144", "fake-token") == 5
    assert any(x.endswith("/issues/2") for x in calls)


def test_github_select_fails_closed_when_issue_list_needs_paging(monkeypatch):
    from axar_build import cli
    monkeypatch.setattr(cli, "_get_json", lambda url, token: [issue(n, labels=["claude-ready"]) for n in range(1,101)])
    with pytest.raises(PlanError, match="pagination required"):
        cli.github_select("Erkki144/axar-network", "Erkki144", "fake")


def test_github_select_rejects_other_owner(monkeypatch):
    from axar_build import cli
    with pytest.raises(PlanError, match="owner mismatch"):
        cli.github_select("someone/repo", "Erkki144", "fake")


def test_github_select_blocks_unclosed_dependency(monkeypatch):
    from axar_build import cli
    def fake_get(url, token):
        return [issue(5, labels=["claude-ready"], body="Depends-On: #2")] if url.endswith("?state=open&per_page=100") else {"state":"open"}
    monkeypatch.setattr(cli, "_get_json", fake_get)
    assert cli.github_select("Erkki144/axar-network", "Erkki144", "fake") is None
