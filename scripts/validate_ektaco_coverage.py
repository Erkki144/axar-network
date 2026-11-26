"""Pure-stdlib coverage guard for the public Ektaco CC5 reference corpus.

Run: python scripts/validate_ektaco_coverage.py
This validates the research manifest, not AXAR business logic or Ektaco internals.
"""
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "docs" / "ektaco" / "CC5_SOURCE_COVERAGE.json"
data = json.loads(PATH.read_text(encoding="utf-8"))
items = data["items"]
till = [x for x in items if x["section"] == "cc5-kassa"]
office = [x for x in items if x["section"] != "cc5-kassa"]
assert len(office) == data["expected_office"] == 64, len(office)
assert len(till) == data["expected_till"] == 7, len(till)
assert len({x["url"] for x in items}) == len(items), "Duplicate URL"
for row in items:
    assert row["title"] and row["section"]
    url = urlsplit(row["url"])
    assert url.scheme == "https" and url.netloc == "ektaco.ee", row
    assert url.path.startswith("/helpcenter/"), row
    assert row["review_level"] == "article_opened", row
assert Counter(x["section"] for x in items)["laohaldus"] == 14
assert any("/veebimenuud/" in x["url"] for x in items)
assert any("/veebimuuk/" in x["url"] for x in items)
assert any("/windowsi-kassa-nupud/" in x["url"] for x in items)
print(f"OK: {len(office)} office + {len(till)} till = {len(items)} unique documented source links")

# Ensure every documented end-to-end dependency has an accountable owner and proof gate.
gap = json.loads((ROOT / "docs" / "ektaco" / "AXAR_GAP_REGISTER.json").read_text(encoding="utf-8"))
edges = gap["edges"]
ids = [e["edge_id"] for e in edges]
assert len(edges) == gap["record_count"] == 52
assert ids == [f"E{i}" for i in range(1, 53)], "Edge order/gap/duplication"
for edge in edges:
    assert edge["primary_issue"].startswith("#")
    assert edge["priority"].startswith("P")
    assert edge["acceptance_evidence_required"] and edge["baseline"]
for code, issue in [("E19", "#69"), ("E27", "#68")]:
    e = next(x for x in edges if x["edge_id"] == code)
    assert e["primary_issue"] == issue
    assert e["baseline"] == "confirmed_missing_main_in_inspected_function"
print(f"OK: {len(edges)} source→AXAR integration edges tracked with proof gates")
