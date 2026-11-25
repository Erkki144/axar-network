"""Offline-first O1a (ADR-0012, #29): hash-chained device journal written in
the money transaction, and per-register receipt series set in settings."""

import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, text

from axar_pos import journal
from axar_pos.models import DeviceJournalEntry, Sale


def verify(api, register="R1"):
    r = api.as_role("manager").get(f"/api/v1/admin/registers/{api.register_id(register)}/journal/verify")
    assert r.status_code == 200, r.text
    return r.json()


def entries(app, register_id):
    with app.state.session_factory() as s:
        return list(s.scalars(select(DeviceJournalEntry).where(
            DeviceJournalEntry.stream_id == journal.stream_for_register(register_id))
            .order_by(DeviceJournalEntry.seq)))


# --------------------------------------------------------------- the chain


def test_every_sale_and_refund_is_journaled_in_order_and_verifies(a, app):
    a.open_drawer(10_000)
    sales = [a.sell([("KOHV", n)], 5000).json() for n in (1, 2, 3)]
    assert a.as_role("manager").refund(sales[1]["id"], [(1, 1)]).status_code == 201
    es = entries(app, a.register_id())
    assert [(e.seq, e.entry_type) for e in es] == [(1, "sale"), (2, "sale"), (3, "sale"), (4, "refund")]
    assert [e.entity_id for e in es[:3]] == [s["id"] for s in sales]
    assert es[0].prev_hash == journal.GENESIS and all(es[i].prev_hash == es[i - 1].hash for i in range(1, 4))
    assert len({e.id for e in es}) == 4 and all(len(e.id) == 26 for e in es)          # ULIDs
    v = verify(a)
    assert (v["ok"], v["entries"], v["problems"]) == (True, 4, [])


def test_registers_have_separate_streams(a, app):
    a.open_drawer(register="R1"); a.open_drawer(register="R2")
    a.sell([("KOHV", 1)], 500, register="R1"); a.sell([("KOHV", 1)], 500, register="R2")
    a.sell([("KOHV", 1)], 500, register="R2")
    assert [e.seq for e in entries(app, a.register_id("R1"))] == [1]
    assert [e.seq for e in entries(app, a.register_id("R2"))] == [1, 2]


def test_failed_sale_burns_no_journal_seq_and_no_register_number(a, app):
    a.open_drawer()
    assert a.sell([("KOHV", 1)], 500).status_code == 201
    assert a.sell([("KOHV", 4)], 100).status_code == 422                 # insufficient tender
    assert a.sell([("KOHV", 1)], 500).status_code == 201
    assert [e.seq for e in entries(app, a.register_id())] == [1, 2]
    with app.state.session_factory() as s:
        assert sorted(s.scalars(select(Sale.register_receipt_no))) == [1, 2]


def test_replayed_request_appends_nothing(a, app):
    a.open_drawer()
    assert a.sell([("KOHV", 1)], 500, key="journal-replay-1").status_code == 201
    assert a.sell([("KOHV", 1)], 500, key="journal-replay-1").status_code == 200
    assert len(entries(app, a.register_id())) == 1


def test_parallel_sales_give_a_gap_free_chain(a, app):
    a.open_drawer(register="R1")
    with ThreadPoolExecutor(8) as pool:
        rs = list(pool.map(lambda _: a.sell([("KOHV", 1)], 500), range(24)))
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs if r.status_code != 201]
    assert [e.seq for e in entries(app, a.register_id())] == list(range(1, 25))
    assert verify(a)["ok"] is True
    assert sorted(r.json()["register_receipt_no"] for r in rs) == list(range(1, 25))


# --------------------------------------------------------- tamper evidence


def _owner_exec(app, sql, **params):
    """Bypass the ORM append-only guard, like a hostile DB admin would."""
    with app.state.session_factory.begin() as s:
        s.execute(text(sql), params)


def test_changed_sale_is_detected(a, app):
    a.open_drawer()
    sale = a.sell([("KOHV", 2)], 1000).json()
    a.sell([("KOHV", 1)], 500)
    _owner_exec(app, "UPDATE sales SET total_cents = total_cents - 100 WHERE id = :i", i=sale["id"])
    v = verify(a)
    assert v["ok"] is False and v["problems"] == [f"seq 1: sale {sale['id']} was changed after it was journaled"]


def test_deleted_entry_and_edited_hash_are_detected(a, app):
    a.open_drawer()
    for _ in range(3):
        a.sell([("KOHV", 1)], 500)
    es = entries(app, a.register_id())
    _owner_exec(app, "UPDATE device_journal SET hash = :h WHERE id = :i", h="f" * 64, i=es[2].id)
    assert verify(a)["problems"] == ["seq 3: entry hash does not match its content"]
    _owner_exec(app, "DELETE FROM device_journal WHERE id = :i", i=es[1].id)
    assert verify(a)["problems"] == ["seq 2 missing (found 3)"]


def test_a_broken_chain_is_not_extended_silently(a, app):
    a.open_drawer()
    a.sell([("KOHV", 1)], 500); a.sell([("KOHV", 1)], 500)
    es = entries(app, a.register_id())
    _owner_exec(app, "DELETE FROM device_journal WHERE id = :i", i=es[1].id)
    r = a.sell([("KOHV", 1)], 500)
    assert r.status_code == 500 and r.json()["error"] == "journal_broken"   # refuses; nothing half-written
    assert len(entries(app, a.register_id())) == 1


def test_journal_rows_are_append_only_through_the_orm(a, app):
    import pytest

    from axar_pos.models import AppendOnlyViolation

    a.open_drawer()
    a.sell([("KOHV", 1)], 500)
    with app.state.session_factory() as s:
        e = s.scalars(select(DeviceJournalEntry)).first()
        e.hash = "0" * 64
        with pytest.raises(AppendOnlyViolation):
            s.flush()


# ------------------------------------------------------ per-register series


def series(api, register="R1", **body):
    m = api.as_role("manager")
    path = f"/api/v1/admin/registers/{api.register_id(register)}/receipt-series"
    return m.c.put(path, headers=m.h, json=body) if body else m.get(path)


def test_series_off_by_default_numbers_still_per_register(a):
    a.open_drawer(register="R1"); a.open_drawer(register="R2")
    s1 = a.sell([("KOHV", 1)], 500, register="R1").json()
    s2 = a.sell([("KOHV", 1)], 500, register="R2").json()
    assert (s1["register_receipt_no"], s1["receipt_label"]) == (1, None)
    assert (s2["register_receipt_no"], s2["receipt_label"]) == (1, None)
    assert "Tšekk nr 2" in a.get(f"/api/v1/sales/{s2['id']}/receipt.txt").text    # unchanged when off


def test_manager_sets_prefix_and_start_and_receipts_use_the_label(a):
    a.open_drawer(register="R1"); a.open_drawer(register="R2")
    a.sell([("KOHV", 1)], 500, register="R1")
    r = series(a, "R1", prefix="K1", next_number=100)
    assert r.status_code == 200, r.text
    assert r.json() == {"register_id": a.register_id("R1"), "prefix": "K1", "last_number": 1,
                        "next_number": 100, "next_label": "K1-000100"}
    sale = a.sell([("KOHV", 1)], 500, register="R1").json()
    assert (sale["register_receipt_no"], sale["receipt_label"]) == (100, "K1-000100")
    assert a.sell([("KOHV", 1)], 500, register="R1").json()["receipt_label"] == "K1-000101"
    assert "Tšekk nr K1-000100" in a.get(f"/api/v1/sales/{sale['id']}/receipt.txt").text
    assert series(a, "R2", prefix="K2", next_number=1).status_code == 200
    assert a.sell([("KOHV", 1)], 500, register="R2").json()["receipt_label"] == "K2-000001"


def test_numbers_are_never_reused_and_changes_are_audited(a, app):
    a.open_drawer()
    for _ in range(3):
        a.sell([("KOHV", 1)], 500)
    r = series(a, prefix="K1", next_number=3)
    assert r.status_code == 409 and "never reused" in r.json()["message"]
    assert series(a, prefix="k-1", next_number=10).status_code == 422          # format
    assert series(a, prefix="K1", next_number=4).status_code == 200
    with app.state.session_factory() as s:
        ev = s.execute(text("SELECT detail FROM auth_events WHERE event = 'receipt_series.changed'")).scalars().all()
    assert len(ev) == 1 and '"prefix": "K1"' in ev[0]


def test_series_needs_a_manager_and_is_tenant_scoped(a, b):
    assert series(b, "R1").status_code == 200                      # b's own register
    other = a.register_id("R1")
    m = b.as_role("manager")
    assert m.get(f"/api/v1/admin/registers/{other}/receipt-series").status_code == 404
    assert m.get(f"/api/v1/admin/registers/{other}/journal/verify").status_code == 404


def test_parallel_appends_without_any_other_lock_stay_gap_free(a, app, settings):
    """Future writers (edge sync ingest) will not hold a drawer lock: the
    journal's own counter must serialize appends by itself."""
    from axar_pos.db import bind_tenant
    from axar_pos.models import Tenant

    def one(i):
        with app.state.request_factory() as s:
            bind_tenant(s, "t-a")
            journal.append(s, tenant_id="t-a", stream_id="device:test", entry_type="sale",
                           entity_id=str(uuid.uuid4()), summary={"i": i}, now=settings.clock())
            s.commit()

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(one, range(16)))
    with app.state.session_factory() as s:
        seqs = list(s.scalars(select(DeviceJournalEntry.seq).where(DeviceJournalEntry.stream_id == "device:test")
                              .order_by(DeviceJournalEntry.seq)))
        es = list(s.scalars(select(DeviceJournalEntry).where(DeviceJournalEntry.stream_id == "device:test")
                            .order_by(DeviceJournalEntry.seq)))
    assert seqs == list(range(1, 17))
    assert all(es[i].prev_hash == es[i - 1].hash for i in range(1, 16))
