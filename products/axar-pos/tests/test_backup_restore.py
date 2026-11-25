"""SEC-2 restore drill (#34), PostgreSQL only: back up a database with real
POS, kitchen, refund and inventory data, restore it into a NEW empty database
and prove (1) every table's content is identical, (2) RLS and grants came
back, (3) the till keeps working on the restored copy with the next receipt
number, and (4) a corrupted dump or a non-empty target is refused."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from axar_pos import backup
from axar_pos.api import create_app
from axar_pos.config import Settings

PG_BASE = os.environ.get("AXAR_TEST_PG")
pytestmark = pytest.mark.skipif(not PG_BASE, reason="PostgreSQL only (set AXAR_TEST_PG)")


@pytest.fixture
def empty_db():
    """A fresh, empty database on the same server (dropped afterwards)."""
    name = f"axar_r_{uuid.uuid4().hex[:12]}"
    admin = create_engine(f"{PG_BASE}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield f"{PG_BASE}/{name}"
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _business_day(a, app, settings):
    """Sales, a refund, a table bill through the kitchen, inventory + worker."""
    from axar_pos import inventory, worker
    from axar_pos.db import bind_tenant

    d = a.open_drawer(10_000)
    for i, lines in enumerate(([("KOHV", 2)], [("SAI", 1), ("KOHV", 1)], [("RAAMAT", 1)])):
        assert a.sell(lines, 5000, key=f"drill-sale-{i:04d}").status_code == 201
    sale = a.sell([("KOHV", 3)], 1000).json()
    assert a.as_role("manager").refund(sale["id"], [(1, 1)]).status_code == 201
    tid = next(t["id"] for t in a.get("/api/v1/tables").json() if t["code"] == "A1")
    o = a.post(f"/api/v1/tables/{tid}/order").json()
    o = a.c.put(f"/api/v1/orders/{o['id']}/lines", headers=a.h, json={"version": o["version"], "lines": [
        {"product_id": a.product_id("KOHV"), "quantity": 2}]}).json()
    k = a.post(f"/api/v1/orders/{o['id']}/kitchen", {"version": o["version"]},
               headers={"Idempotency-Key": "restore-drill-kitchen"}).json()
    assert a.post(f"/api/v1/kitchen/tickets/{k['ticket']['id']}/ready").status_code == 200
    with app.state.request_factory() as s:
        bind_tenant(s, "t-a")
        loc = a.get("/api/v1/registers").json()[0]["location"]["id"]
        wh = inventory.create_warehouse(s, "t-a", location_id=loc, code="W1", name="Ladu", is_default=True)
        it = inventory.create_item(s, "t-a", sku="PIIM", name="Piim", kind="ingredient", uom="l")
        sp = inventory.create_supplier(s, "t-a", code="S1", name="Tarnija")
        now = settings.clock()
        doc = inventory.create_document(s, "t-a", supplier_id=sp.id, doc_type="invoice", doc_no="R-1",
                                        doc_date=now.date(), warehouse_id=wh.id,
                                        lines=[inventory.DocLine(it.id, 5_000_000, 600, 2400)],
                                        freight_cents=0, actor="k", now=now)
        inventory.post_document(s, "t-a", doc.id, actor="k", now=now)
    worker.run_once(app.state.request_factory, settings, "drill")
    return d


def test_backup_restores_identical_content_rls_grants_and_keeps_selling(a, app, settings, tmp_path, empty_db):
    _business_day(a, app, settings)
    last_receipt = max(s["receipt_no"] for s in a.get("/api/v1/sales").json())
    source_admin = settings.admin_database_url

    dump_path = tmp_path / "pos.dump"
    manifest = backup.dump(source_admin, dump_path)
    assert manifest["tables"]["sales"]["rows"] == 4 and manifest["tables"]["refunds"]["rows"] == 1
    assert manifest["tables"]["stock_movements"]["rows"] >= 1
    assert manifest["alembic_revision"]

    backup.restore(empty_db, dump_path)
    assert backup.verify(empty_db, dump_path) == []                 # every table identical

    # RLS + grants came back: as the app role, nothing is visible without a tenant
    app_url = make_url(settings.database_url).set(database=make_url(empty_db).database) \
        .render_as_string(hide_password=False)
    eng = create_engine(app_url)
    with eng.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM sales")) == 0
        c.execute(text("SELECT set_config('app.tenant_id', 't-a', true)"))
        assert c.scalar(text("SELECT count(*) FROM sales")) == 4
    eng.dispose()

    # The till keeps working on the restored copy, with the NEXT receipt number
    restored = create_app(Settings(database_url=app_url, admin_database_url=empty_db,
                                   demo_identity=True, clock=settings.clock))
    try:
        with TestClient(restored) as c:
            h = {"X-Tenant-ID": "t-a", "X-Cashier-ID": "kassa1"}
            reg = next(r["id"] for r in c.get("/api/v1/registers", headers=h).json() if r["code"] == "R1")
            pid = next(p["id"] for p in c.get("/api/v1/products", headers=h).json() if p["sku"] == "KOHV")
            r = c.post("/api/v1/sales/cash", headers={**h, "Idempotency-Key": "after-restore-0001"},
                       json={"register_id": reg, "lines": [{"product_id": pid, "quantity": 1}], "tendered_cents": 500})
            assert r.status_code == 201, r.text
            assert r.json()["receipt_no"] == last_receipt + 1
            # the original idempotency keys are restored too: a replay returns the
            # ORIGINAL sale instead of selling again
            original = a.get("/api/v1/sales").json()
            first = next(s for s in original if s["receipt_no"] == 1)
            body = {"register_id": reg, "lines": [{"product_id": pid, "quantity": 2}], "tendered_cents": 5000}
            again = c.post("/api/v1/sales/cash", headers={**h, "Idempotency-Key": "drill-sale-0000"}, json=body)
            assert again.status_code == 200 and again.json()["id"] == first["id"]
            assert c.get("/api/v1/operations/drill-sale-0001", headers=h).json()["status"] == "recorded"
    finally:
        restored.state.engine.dispose()
        restored.state.admin_engine.dispose()


def test_corrupted_dump_and_non_empty_target_are_refused(a, app, settings, tmp_path, empty_db):
    a.open_drawer()
    assert a.sell([("KOHV", 1)], 250).status_code == 201
    dump_path = tmp_path / "pos.dump"
    backup.dump(settings.admin_database_url, dump_path)

    with pytest.raises(backup.BackupError, match="not empty"):
        backup.restore(settings.admin_database_url, dump_path)          # the live source itself

    data = bytearray(dump_path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    dump_path.write_bytes(bytes(data))
    with pytest.raises(backup.BackupError, match="checksum mismatch"):
        backup.restore(empty_db, dump_path)
    eng = create_engine(empty_db)
    with eng.connect() as c:                                            # nothing was written
        assert c.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")) == 0
    eng.dispose()


def test_verify_detects_a_changed_restore(a, app, settings, tmp_path, empty_db):
    a.open_drawer()
    assert a.sell([("KOHV", 1)], 250).status_code == 201
    dump_path = tmp_path / "pos.dump"
    backup.dump(settings.admin_database_url, dump_path)
    backup.restore(empty_db, dump_path)
    eng = create_engine(empty_db)
    with eng.begin() as c:
        c.execute(text("UPDATE products SET price_cents = price_cents + 1 WHERE sku = 'KOHV'"))
    eng.dispose()
    diffs = backup.verify(empty_db, dump_path)
    assert len(diffs) == 1 and diffs[0].startswith("products:")


def test_credentials_never_go_on_the_command_line():
    env = backup._libpq_env("postgresql+psycopg://owner:s3cret@db.example:5433/pos")
    assert (env["PGUSER"], env["PGPASSWORD"], env["PGHOST"], env["PGPORT"], env["PGDATABASE"]) == (
        "owner", "s3cret", "db.example", "5433", "pos")
    with pytest.raises(backup.BackupError):
        backup._libpq_env("sqlite:///x.db")


def test_client_tools_must_match_the_server_major_version(a, settings, tmp_path, monkeypatch):
    """A pg_dump newer than the server writes SQL the server rejects (seen in CI:
    pg_dump 17+ emits SET transaction_timeout that PostgreSQL 16 refuses);
    refuse a mismatched tool instead of producing an unrestorable backup."""
    fake = tmp_path / "bin"
    fake.mkdir()
    tool = fake / "pg_dump"
    tool.write_text('#!/bin/sh\necho "pg_dump (PostgreSQL) 17.2"\n')
    tool.chmod(0o755)
    assert backup._tool_major(str(tool)) == 17                 # parses real --version output
    # The local tools match the local server ...
    assert backup._tool_major(backup.pg_tool("pg_dump", settings.admin_database_url)) == \
        backup._server_major(settings.admin_database_url)
    # ... and a server of another major version gets a clear refusal, no dump file.
    monkeypatch.setattr(backup, "_server_major", lambda url: 99)
    monkeypatch.setenv("AXAR_POS_PG_BIN", str(fake))
    out = tmp_path / "x.dump"
    with pytest.raises(backup.BackupError, match="no pg_dump for PostgreSQL 99"):
        backup.dump(settings.admin_database_url, out)
    assert not out.exists()
