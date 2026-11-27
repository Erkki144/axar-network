"""Guards (integration architect): every event the code can emit has a committed
contract, and every database table is either checked by a reconciliation rule
or explicitly declared as not needing one.

These tests read the source and the schema, not a running system, so they
fail in CI the moment someone adds an event or a table and forgets the other
half (e.g. PR #71: ``drawer.cash.moved`` without a schema).
"""

from __future__ import annotations

import ast
from pathlib import Path

from axar_pos import reconcile
from axar_pos.models import Base

PKG = Path(__file__).resolve().parents[1] / "axar_pos"
SCHEMAS = Path(__file__).resolve().parents[2] / "axar-one" / "architecture" / "contracts" / "events"


# ------------------------------------------------------------------ events


def _str_values(node: ast.AST) -> set[str] | None:
    """Constant strings an expression can take (constants and if/else)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        a, b = _str_values(node.body), _str_values(node.orelse)
        return a | b if a is not None and b is not None else None
    return None


def _emitted_events() -> tuple[set[tuple[str, int]], list[str]]:
    """``{(type, version)}`` for every ``emit(...)`` in the package, following
    one level of helper functions whose parameter is passed as ``type=``.
    Returns also the places whose type could not be resolved statically."""
    found: set[tuple[str, int]] = set()
    unresolved: list[str] = []
    for f in sorted(PKG.rglob("*.py")):
        if "migrations" in f.parts:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        for fn in funcs.values():
            for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
                name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
                if name != "emit":
                    continue
                kw = {k.arg: k.value for k in call.keywords}
                if "type" not in kw:
                    continue
                version = kw["version"].value if isinstance(kw.get("version"), ast.Constant) else 1
                values = _str_values(kw["type"])
                if values is not None:
                    found |= {(v, version) for v in values}
                    continue
                param = kw["type"].id if isinstance(kw["type"], ast.Name) else None
                args = [a.arg for a in fn.args.args]
                if param not in args:
                    unresolved.append(f"{f.name}:{call.lineno}")
                    continue
                pos = args.index(param)
                for c in calls:
                    if getattr(c.func, "id", None) != fn.name:
                        continue
                    arg = next((k.value for k in c.keywords if k.arg == param), None)
                    if arg is None and len(c.args) > pos:
                        arg = c.args[pos]
                    vals = _str_values(arg) if arg is not None else None
                    if vals is None:
                        unresolved.append(f"{f.name}:{c.lineno}")
                    else:
                        found |= {(v, version) for v in vals}
    return found, unresolved


def test_every_emitted_event_has_a_committed_schema():
    found, unresolved = _emitted_events()
    assert unresolved == [], f"event type not a literal; make it one so the guard can check it: {unresolved}"
    assert len(found) >= 10, found  # the scanner itself works
    missing = sorted(f"{t}.v{v}.schema.json" for t, v in found if not (SCHEMAS / f"{t}.v{v}.schema.json").exists())
    assert missing == [], f"emitted events without a contract in contracts/events: {missing}"


def test_every_schema_belongs_to_an_event_the_code_can_emit():
    found, _ = _emitted_events()
    names = {f"{t}.v{v}.schema.json" for t, v in found}
    # Older versions stay while consumers migrate (04-event-catalog.md rule 2); list them here on purpose.
    kept_for_consumers = {"envelope.v2.schema.json", "order.changed.v1.schema.json", "order.closed.v1.schema.json"}
    orphans = sorted(p.name for p in SCHEMAS.glob("*.schema.json") if p.name not in names | kept_for_consumers)
    assert orphans == [], f"schemas no code emits (remove, or keep on purpose in this list): {orphans}"


# ------------------------------------------------------------------ registers

# Every table, and which reconciliation rule watches it. A table with money,
# stock, documents or published prices must name a rule; anything else must
# say why it does not need one. A new table fails here until it is classified.
TABLE_COVERAGE: dict[str, str] = {
    # money and sales
    "sales": "R2.sale_totals", "sale_lines": "R2.sale_totals", "drawer_sessions": "R1.drawer_cash",
    "payments": "R3.tenders", "refunds": "R4.refunds", "refund_lines": "R4.refunds",
    "refund_allocations": "R4.refunds", "sale_refund_state": "R4.refunds",
    "tenant_counters": "R5.numbering", "customer_invoices": "R12.receivables",
    "bank_transactions": "R12.receivables", "mock_terminal_txns": "R3.tenders",
    # events and exports
    "outbox_events": "R6.events_per_document", "event_consumers": "R7.event_pipeline",
    "event_inbox": "R7.event_pipeline", "event_dead_letters": "R7.event_pipeline", "export_queue": "R8.books_export",
    # stock
    "stock_movements": "R10.stock_ledger", "stock_balances": "R10.stock_ledger",
    "recipes": "R9.stock_consumption", "recipe_lines": "R9.stock_consumption",
    "production_batches": "R10.stock_ledger", "supplier_documents": "R10.stock_ledger",
    "drawer_cash_movements": "R1.drawer_cash",
    "supplier_document_lines": "R10.stock_ledger",
    # orders and kitchen
    "orders": "R11.orders_kitchen", "order_lines": "R11.orders_kitchen",
    "kitchen_tickets": "R11.orders_kitchen", "kitchen_ticket_lines": "R11.orders_kitchen",
    # offline tills
    "device_journal": "R13.offline_sync", "device_inbox": "R13.offline_sync", "device_cursors": "R13.offline_sync",
    # prices (P1-1a)
    "price_versions": "R14.sale_prices", "products": "R14.sale_prices",
}
NOT_A_LEDGER: dict[str, str] = {
    "tenants": "reference data", "locations": "reference data", "registers": "reference data",
    "tax_rates": "versioned reference data, read-only for the app",
    "dining_tables": "reference data", "warehouses": "reference data", "items": "reference data",
    "suppliers": "reference data", "product_groups": "display tree; no money or stock",
    "product_barcodes": "lookup keys", "price_lists": "names only; prices live in price_versions (R14)",
    "staff": "identity; audited in auth_events", "staff_roles": "identity; audited in auth_events",
    "devices": "identity; audited in auth_events", "auth_sessions": "identity; audited in auth_events",
    "enrollment_codes": "one-time codes; audited in auth_events", "approvals": "one-time step-up proofs",
    "auth_events": "append-only audit log itself",
    "pos_settings": "configuration; changes audited",
    "edge_sync_state": "edge-node cursor; R13 checks the documents it produced",
    "sync_outbox": "edge-node queue; R13 checks central effects",
}


def test_every_table_is_watched_by_a_rule_or_declared_not_a_ledger():
    tables = set(Base.metadata.tables)
    both = set(TABLE_COVERAGE) & set(NOT_A_LEDGER)
    assert both == set(), f"classified twice: {both}"
    unclassified = sorted(tables - set(TABLE_COVERAGE) - set(NOT_A_LEDGER))
    assert unclassified == [], (f"new tables need a reconciliation rule (TABLE_COVERAGE) or a reason "
                                f"(NOT_A_LEDGER): {unclassified}")
    stale = sorted((set(TABLE_COVERAGE) | set(NOT_A_LEDGER)) - tables)
    assert stale == [], f"classified tables that no longer exist: {stale}"


def test_every_named_rule_exists():
    rules = {f.__name__ for f in reconcile.RULES}
    assert len(rules) == len(reconcile.RULES)
    ids = set()
    from axar_pos.db import init_db, make_engine

    factory = init_db(make_engine("sqlite://"))
    with factory() as s:
        ids = {c["check"] for c in reconcile.check(s, "nobody")["checks"]}
    unknown = sorted(set(TABLE_COVERAGE.values()) - ids)
    assert unknown == [], f"TABLE_COVERAGE names rules that do not exist: {unknown}"
