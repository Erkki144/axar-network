"""ATLAS architecture is source-backed, internally coherent and honestly not live."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ATLAS = ROOT / "products" / "axar-one" / "atlas"


def model():
    raw = (ATLAS / "graph-data.js").read_text(encoding="utf-8")
    assert raw.startswith("window.AXAR_ATLAS_DATA = ")
    return json.loads(raw.split("=", 1)[1].strip().removesuffix(";"))


def test_atlas_has_source_backed_nodes_edges_and_valid_owners():
    m = model()
    assert m["meta"]["runtimeConnected"] is False
    assert len(m["nodes"]) >= 18 and len(m["edges"]) >= 24
    ids = [n["id"] for n in m["nodes"]]
    assert len(ids) == len(set(ids)), "duplicate node id"
    index = {n["id"]: n for n in m["nodes"]}
    for n in m["nodes"]:
        assert n["title"] and n["owner"] and n["description"]
        assert n["status"] in m["statuses"], (n["id"], n["status"])
        assert n["kind"] in {"core", "interface", "device", "domain", "governance"}
        if n["status"] in ("code", "partial"):
            assert n["sources"], f"{n['id']}: code/partial node must name its source"
        if n["status"] == "planned":
            assert n["issues"], f"{n['id']}: planned node needs tracking issue"
        for p in n["sources"]:
            assert not p.startswith(("http:", "https:", "/"))
            assert (ROOT / p).exists(), (n["id"], p)
    for e in m["edges"]:
        assert e["from"] in index and e["to"] in index
        assert e["from"] != e["to"]
        assert e["label"] and e["type"]
        assert e["status"] in m["statuses"]
    for f in m["flows"]:
        assert len(f["nodes"]) >= 3
        assert all(x in index for x in f["nodes"])
        assert f["note"]


def test_atlas_has_zero_live_monitoring_claims_or_external_runtime_dependencies():
    html = (ATLAS / "index.html").read_text(encoding="utf-8")
    js = (ATLAS / "app.js").read_text(encoding="utf-8")
    css = (ATLAS / "style.css").read_text(encoding="utf-8")
    assert 'src="graph-data.js"' in html and 'src="app.js"' in html
    assert 'href="style.css"' in html
    assert "LIVE ÜHENDAMATA" in html
    assert "runtimeConnected:false" not in (ATLAS / "graph-data.js").read_text()
    assert '"runtimeConnected": false' in (ATLAS / "graph-data.js").read_text()
    assert not any(x in js for x in ("fetch(", "XMLHttpRequest", "EventSource(", "WebSocket("))
    assert "AXAR_ATLAS_TEST" in js
    assert "role:\"button\"" in js and 'e.key==="Enter"' in js
    assert "prefers-reduced-motion" in css


def test_atlas_connected_modules_dont_claim_prod_ready():
    m=model()
    for node in m["nodes"]:
        text=(node["title"] + " " + node["description"]).lower()
        assert "100% kindel" not in text
        assert "production-ready" not in text
