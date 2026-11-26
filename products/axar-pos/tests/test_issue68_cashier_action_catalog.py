"""The 80 cashier controls are an audited design contract, not an assertion of implementation."""
import json
from collections import Counter
from pathlib import Path

CAT = Path(__file__).resolve().parents[3] / "docs" / "issue68" / "CASHIER_ACTION_CATALOG.json"


def test_cashier_ui_action_inventory_is_exhaustive_and_versioned():
    data = json.loads(CAT.read_text(encoding="utf-8"))
    actions = data["actions"]
    assert data["issue"] == 68 and data["schema_version"] == 1
    assert len(actions) == 80
    assert len({a["action_id"] for a in actions}) == 80
    assert Counter(a["group"] for a in actions) == {
        "nav": 11, "topbar": 7, "product": 10, "bill": 19,
        "payment": 13, "drawer": 8, "device": 12,
    }
    accepted = {"MAIN_UI", "MAIN_API", "PARTIAL_MAIN", "MISSING_MAIN", "PROVIDER_DEPENDENT"}
    for a in actions:
        assert a["label"] and a["code_baseline"] in accepted
        assert a["permission_candidate"] and a["degraded_mode"] and a["work_note"]
    assert {a["action_id"] for a in actions if a["label"].startswith("Sularaha ")} >= {"C02", "C03"}


def test_cash_actions_remain_honest_about_main_and_are_in_draft_pr():
    rows = {a["action_id"]: a for a in json.loads(CAT.read_text(encoding="utf-8"))["actions"]}
    for action_id in ("C02", "C03"):
        assert rows[action_id]["code_baseline"] == "MISSING_MAIN"
        assert rows[action_id]["existing_api_main"] is None
        assert rows[action_id]["draft_pr"] == 71
        assert rows[action_id]["implemented_permission"] == "drawer"
