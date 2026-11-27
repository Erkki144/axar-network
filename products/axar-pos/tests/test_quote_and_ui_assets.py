"""Issue #11 backend support: server price quote and the AXAR ONE UI assets."""

import re
from pathlib import Path

import pytest
from sqlalchemy import func, select

from axar_pos.models import OutboxEvent, Sale

STATIC_ONE = Path(__file__).resolve().parents[1] / "axar_pos" / "static" / "one"
STATIC_OFFICE = STATIC_ONE.parent / "office"   # #76: separate office application


def quote(api, lines):
    return api.post("/api/v1/sales/quote",
                    {"lines": [{"product_id": api.product_id(sku), "quantity": q} for sku, q in lines]})


def test_quote_matches_the_real_sale_exactly(a):
    a.open_drawer()
    q = quote(a, [("KOHV", 2), ("SAI", 1), ("RAAMAT", 1)])
    assert q.status_code == 200, q.text
    q = q.json()
    sale = a.sell([("KOHV", 2), ("SAI", 1), ("RAAMAT", 1)], 3000).json()
    for k in ("total_cents", "vat_cents", "net_cents", "cash_rounding_cents", "cash_due_cents"):
        assert q[k] == sale[k], k
    assert [(x["gross_cents"], x["vat_cents"], x["vat_rate_bp"]) for x in q["lines"]] == \
        [(x["gross_cents"], x["vat_cents"], x["vat_rate_bp"]) for x in sale["lines"]]
    assert q["binding"] is False


def test_quote_writes_nothing_and_needs_no_open_drawer(a, app):
    r = quote(a, [("KOHV", 1)])  # drawer closed: quoting is still allowed
    assert r.status_code == 200
    with app.state.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Sale)) == 0
        assert s.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_quote_is_tenant_scoped(a, b):
    r = a.post("/api/v1/sales/quote",
               {"lines": [{"product_id": b.product_id("B-SECRET"), "quantity": 1}]})
    assert r.status_code == 404


@pytest.mark.parametrize("body", [
    {"lines": []},
    {"lines": [{"product_id": 1, "quantity": 0}]},
    {"lines": [{"product_id": 1, "quantity": 1.0}]},
    {"lines": [{"product_id": 1, "quantity": 1, "price_cents": 1}]},
    {"lines": [{"product_id": 1, "quantity": 1}], "total_cents": 1},
])
def test_quote_rejects_bad_input(a, body):
    assert a.post("/api/v1/sales/quote", body).status_code == 422


def test_quote_requires_identity(client):
    assert client.post("/api/v1/sales/quote", json={"lines": [{"product_id": 1, "quantity": 1}]}).status_code == 401


def test_products_expose_menu_category(a):
    cats = {p["sku"]: p["category"] for p in a.get("/api/v1/products").json()}
    assert cats["KOHV"] == "Muud"  # fixture products use the default category


def test_ui_assets_served_with_safe_headers(client):
    r = client.get("/one/app.mjs")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/one/").status_code == 200
    assert client.get("/one/styles.css").status_code == 200


def test_ui_has_no_client_side_money_authority():
    """Guard against re-introducing browser-side VAT/rounding (AION contract)."""
    src = "\n".join(p.read_text(encoding="utf-8") for p in [*STATIC_ONE.glob("*.mjs"), *STATIC_OFFICE.glob("*.mjs")])
    for forbidden in ("vatIncluded", "cashRounded", "cartSummary", "rateBp", "vat_from_gross"):
        assert forbidden not in src, forbidden
    # Every money POST goes through one function that always sends the
    # journaled Idempotency-Key: sale, bill tender (C3), refund, cash move and recovery.
    assert "'Idempotency-Key': rec.key" in src
    assert src.count("await runMoneyOperation(") == 5  # #68 physical cash move uses the same durable journal
    # Every other write is a NON-money endpoint; money paths are only ever sent
    # as rec.path from the journal (sale, bill payment, refund, cash movement).
    writes = re.findall(r"(?:api|call)\((`[^`]*`|'[^']*'|[\w.]+), \{ method: '(POST|PUT)'", src)
    paths = sorted(p for p, _ in writes)
    assert paths == sorted([
        "rec.path",
        "'/api/v1/sales/quote'", "'/api/v1/drawer/open'", "`/api/v1/drawer/${d.id}/close`",
        "`/api/v1/tables/${id}/order`", "`/api/v1/orders/${order.id}/lines`",
        "`/api/v1/orders/${o.id}/kitchen`", "`/api/v1/kitchen/tickets/${tk.id}/ready`",
        # identity (RFC-0002): enrol, PIN login, logout, two step-up approvals
        "'/api/v1/auth/devices/enroll'", "'/api/v1/auth/pin-login'", "'/api/v1/auth/logout'",
        "'/api/v1/auth/approvals'", "'/api/v1/auth/approvals'",
        # inventory screens (#14): master data, invoice draft/post, recipes
        "'/api/v1/inventory/suppliers'", "'/api/v1/inventory/items'", "'/api/v1/inventory/documents'",
        "`/api/v1/inventory/documents/${d.id}/post`", "'/api/v1/inventory/recipes/preview'",
        "'/api/v1/inventory/recipes'",
        # C3: ask the (mock) provider about a lost card answer; creates no money
        "`/api/v1/payments/${paymentId}/reconcile`",
        # office (#60 P0-2): back-office login / logout, POS settings, offline review queue
        "'/api/v1/auth/login'", "'/api/v1/auth/logout'", "'/api/v1/admin/pos-settings'",
        # office application (#76): its own login / logout
        "'/api/v1/auth/login'", "'/api/v1/auth/logout'",
        "`/api/v1/sync/quarantine/${r.id}/retry`", "`/api/v1/sync/quarantine/${r.id}/dismiss`",
        # catalog + prices (P1-1a): card fields and price PROPOSALS; approval is a second person's
        "'/api/v1/catalog/products'", "`/api/v1/catalog/products/${p.id}`", "'/api/v1/catalog/groups'",
        "'/api/v1/pricing/prices'", "`/api/v1/pricing/prices/${v.id}/approve`",
        "`/api/v1/pricing/prices/${v.id}/reject`", "`/api/v1/pricing/prices/${v.id}/withdraw`",
        "'/api/v1/pricing/lists'", "`/api/v1/pricing/outlets/${o.id}`",
    ]), paths
    assert src.count("method: 'POST'") + src.count("method: 'PUT'") == len(writes)
    for money in ("/api/v1/sales/cash'", "/pay-cash`", "/refunds`", "/payments`"):
        assert all(money not in p for p in paths), money
    # No silent success: the only "Makse kinnitatud" is rendered from the server response.
    assert len(re.findall(r"Makse kinnitatud", src)) >= 1


def test_till_and_office_are_separate_applications():
    """#76: the till bundle has no office code or navigation; the office bundle
    has no till money operations. Shared: design system and format helpers."""
    till_html = (STATIC_ONE / "index.html").read_text(encoding="utf-8")
    till_js = "\n".join(p.read_text(encoding="utf-8") for p in STATIC_ONE.glob("*.mjs"))
    for forbidden in ('data-mode="office"', 'data-mode="inventory"'):
        assert forbidden not in till_html, forbidden
    for forbidden in ("office.mjs", "office-catalog.mjs", "inventory.mjs", "createOffice", "createInventory",
                      "/api/v1/catalog/", "/api/v1/pricing/", "/api/v1/inventory/", "/api/v1/admin/"):
        assert forbidden not in till_js, forbidden
    assert not (STATIC_ONE / "office.mjs").exists() and not (STATIC_ONE / "inventory.mjs").exists()
    office_js = "\n".join(p.read_text(encoding="utf-8") for p in STATIC_OFFICE.glob("*.mjs"))
    for forbidden in ("/api/v1/sales/cash", "/pay-cash", "/movements", "/api/v1/drawer/open", "runMoneyOperation",
                      "/api/v1/auth/pin-login", "/api/v1/tables"):
        assert forbidden not in office_js, forbidden
    imports = [l for l in office_js.splitlines() if l.startswith("import ")]
    assert all("'./" in l or "'../one/format.mjs'" in l for l in imports), imports


def test_office_and_pos_urls(client):
    assert client.get("/office/").status_code == 200
    assert "AXAR ONE — kontor" in client.get("/office/").text
    r = client.get("/pos/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/one/"
