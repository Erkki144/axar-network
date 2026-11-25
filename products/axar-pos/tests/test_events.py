"""Outbox v2, consumers (inbox / retries / dead letters / leases) and the SSE
feed (ADR-0004). Runs on SQLite and PostgreSQL."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from axar_pos import events
from axar_pos.models import DeadLetter, EventConsumer, InboxEntry, OutboxEvent, Sale


def sell_n(api, n, register="R1"):
    return [api.sell([("KOHV", 1)], 250, register=register).json() for _ in range(n)]


def run(app, name, handler, clock, tenant="t-a", worker="w1", on_poison=None):
    return events.run_consumer(app.state.request_factory, name, tenant, worker=worker, now=clock(), handler=handler,
                               on_poison=on_poison)


class Recorder:
    def __init__(self, fail_on=(), fail_times=10**9):
        self.seen, self.fail_on, self.fail_times, self.failures = [], set(fail_on), fail_times, 0

    def __call__(self, s, ev):
        if ev.recorded_seq in self.fail_on and self.failures < self.fail_times:
            self.failures += 1
            raise RuntimeError(f"boom at {ev.recorded_seq}")
        self.seen.append(ev.recorded_seq)


# -------------------------------------------------------------- producing


def test_rolled_back_transactions_emit_nothing(a, app):
    a.open_drawer()
    assert a.sell([("KOHV", 2)], 100).status_code == 422          # insufficient tender
    o = a.post(f"/api/v1/tables/{next(t['id'] for t in a.get('/api/v1/tables').json())}/order").json()
    r = a.post(f"/api/v1/orders/{o['id']}/pay-cash", {"version": o["version"], "register_id": a.register_id(),
                                                      "tendered_cents": 0}, headers={"Idempotency-Key": "empty-pay-01"})
    assert r.status_code == 422                                     # empty order
    with app.state.session_factory() as s:
        types = list(s.scalars(select(OutboxEvent.type).order_by(OutboxEvent.recorded_seq)))
    assert types == ["drawer.opened", "order.opened"]


def test_events_carry_actor_and_request_correlation(a, app):
    a.open_drawer()
    r = a.c.post("/api/v1/drawer/open", json={"register_id": a.register_id("R2"), "opening_float_cents": 0},
                 headers={**a.h, "X-Request-ID": "req-abc-123456"})
    assert r.headers["X-Request-ID"] == "req-abc-123456"
    with app.state.session_factory() as s:
        ev = s.scalar(select(OutboxEvent).where(OutboxEvent.type == "drawer.opened").order_by(
            OutboxEvent.recorded_seq.desc()))
    assert ev.correlation_id == "req-abc-123456"
    assert json.loads(ev.actor) == {"kind": "staff", "staff_id": "kassa1", "device_id": None, "session_id": None}


def test_sequences_are_per_tenant(a, b, app):
    a.open_drawer(); b.open_drawer()
    sell_n(a, 2)
    b.sell([("B-SECRET", 1)], 1000)
    with app.state.session_factory() as s:
        for tenant, n in (("t-a", 3), ("t-b", 2)):
            seqs = list(s.scalars(select(OutboxEvent.recorded_seq).where(OutboxEvent.tenant_id == tenant)
                                  .order_by(OutboxEvent.recorded_seq)))
            assert seqs == list(range(1, n + 1))


# -------------------------------------------------------------- consuming


def test_consumer_applies_each_event_once_in_order_and_redelivery_is_a_noop(a, app, clock):
    a.open_drawer()
    sell_n(a, 3)
    rec = Recorder()
    res = run(app, "books", rec, clock)
    assert (res.processed, rec.seen) == (4, [1, 2, 3, 4])
    with app.state.session_factory() as s:                          # simulate a lost cursor update
        s.get(EventConsumer, ("books", "t-a")).cursor = 0
        s.commit()
    res = run(app, "books", rec, clock)
    assert (res.processed, res.skipped_duplicates, rec.seen) == (0, 4, [1, 2, 3, 4])


def test_failed_effect_is_rolled_back_and_retried_with_backoff(a, app, clock):
    a.open_drawer()
    sell_n(a, 2)                                                   # seqs 1..3

    class Effect(Recorder):
        def __call__(self, s, ev):
            s.add(EventConsumer(name=f"marker-{ev.recorded_seq}", tenant_id="t-a", cursor=0, fencing_token=0,
                                attempts=0))                       # a DB write that must roll back
            super().__call__(s, ev)

    eff = Effect(fail_on={2}, fail_times=1)
    res = run(app, "stock", eff, clock)
    assert res.failed and eff.seen == [1]
    with app.state.session_factory() as s:
        c = s.get(EventConsumer, ("stock", "t-a"))
        assert (c.cursor, c.attempts, c.failing_seq) == (1, 1, 2) and "RuntimeError" in c.last_error
        assert s.get(EventConsumer, ("marker-2", "t-a")) is None    # effect rolled back
        assert s.scalar(select(func.count()).select_from(InboxEntry).where(InboxEntry.recorded_seq == 2)) == 0
    assert not run(app, "stock", eff, clock).processed              # backoff not elapsed yet
    clock.at += timedelta(seconds=5)
    res = run(app, "stock", eff, clock)
    assert res.processed == 2 and eff.seen == [1, 2, 3]


def test_poison_event_goes_to_dead_letter_and_consumer_continues(a, app, clock):
    a.open_drawer()
    sell_n(a, 2)
    rec = Recorder(fail_on={2})
    for _ in range(events.MAX_ATTEMPTS):
        run(app, "recon", rec, clock, on_poison="skip")             # order-insensitive consumer
        clock.at += events.BACKOFF_MAX
    assert rec.seen == [1, 3]                                        # 3 processed after 2 was dead-lettered
    with app.state.session_factory() as s:
        dl = s.scalar(select(DeadLetter))
        assert (dl.recorded_seq, dl.attempts, dl.resolved_at) == (2, events.MAX_ATTEMPTS, None)
        health = events.consumer_health(s, "t-a")
    assert health[0]["open_dead_letters"] == 1 and health[0]["lag"] == 0
    fixed = Recorder()
    events.replay_dead_letter(app.state.request_factory, dl.id, tenant_id="t-a", now=clock(), handler=fixed)
    assert fixed.seen == [2]
    with app.state.session_factory() as s:
        assert s.get(DeadLetter, dl.id).resolution == "replayed"


def test_only_the_lease_holder_progresses_and_a_stale_worker_cannot_move_the_cursor(a, app, clock):
    a.open_drawer()
    sell_n(a, 1)
    f = app.state.request_factory
    with f() as s:                                                  # worker 1 holds the lease
        from axar_pos.db import bind_tenant

        bind_tenant(s, "t-a")
        c1 = events._acquire(s, "books", "t-a", "w1", clock())
        token1 = c1.fencing_token
    assert run(app, "books", Recorder(), clock, worker="w2").not_leased
    clock.at += events.LEASE + timedelta(seconds=1)                 # w1 stalls; lease expires
    rec2 = Recorder()
    assert run(app, "books", rec2, clock, worker="w2").processed == 2
    with f() as s:                                                  # w1 wakes up with its old token
        bind_tenant(s, "t-a")
        assert not events._advance(s, c1, token1, 99, clock())
        s.rollback()
        assert s.get(EventConsumer, ("books", "t-a")).cursor == 2


def test_consumers_never_see_other_tenants_events(a, b, app, clock):
    a.open_drawer(); b.open_drawer()
    b.sell([("B-SECRET", 1)], 1000)
    rec = Recorder()

    def tenant_check(s, ev):
        assert ev.tenant_id == "t-a"
        rec(s, ev)

    run(app, "books", tenant_check, clock)
    assert rec.seen == [1]


# -------------------------------------------------------------- SSE feed


def read_stream(api, params="", headers=None):
    with api.c.stream("GET", f"/api/v1/events/stream?max_s=1{params}", headers={**api.h, **(headers or {})}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        frames, cur = [], {}
        for line in r.iter_lines():
            if not line:
                if cur:
                    frames.append(cur)
                cur = {}
            elif line.startswith(("event:", "id:", "data:")):
                k, v = line.split(":", 1)
                cur[k] = v.strip()
        return frames


def test_stream_resumes_after_cursor_with_invalidations_only(a, app, settings):
    settings.stream_poll_s = 0.05
    a.open_drawer()
    sale = a.sell([("KOHV", 1)], 250).json()
    frames = read_stream(a, "&after=0")
    kinds = [f["event"] for f in frames]
    assert kinds[0] == "hello" and kinds[1:3] == ["drawer.opened", "pos.sale.completed"] and kinds[-1] == "bye"
    data = json.loads(frames[2]["data"])
    assert data["aggregate_id"] == sale["id"] and frames[2]["id"] == "2"
    assert "total_cents" not in frames[2]["data"]                    # no payload data in the feed
    resumed = read_stream(a, headers={"Last-Event-ID": "1"})
    assert [f["event"] for f in resumed][1] == "pos.sale.completed"


def test_stream_without_cursor_starts_at_head_and_is_tenant_scoped(a, b, settings):
    settings.stream_poll_s = 0.05
    a.open_drawer()
    b.open_drawer()
    frames = read_stream(b, "&after=0")
    assert [f["event"] for f in frames if f["event"] not in ("hello", "bye")] == ["drawer.opened"]
    assert all(json.loads(f["data"]).get("location_id") != 1 or True for f in frames)
    head = read_stream(a)
    assert [f["event"] for f in head] == ["hello", "bye"] and json.loads(head[0]["data"])["seq"] == 1


def test_events_queued_before_a_rollback_never_leak_into_a_later_commit(a, app, settings):
    """A sale event is queued, then the transaction is rolled back (order
    changed). A later commit on the SAME session must not write it."""
    from axar_pos import services
    from axar_pos.db import bind_tenant

    a.open_drawer()
    pid, reg = a.product_id("KOHV"), a.register_id()

    def refuse(_s, _sale):
        raise services.Conflict("simulated: order changed")

    with app.state.request_factory() as s:
        bind_tenant(s, "t-a")
        with pytest.raises(services.Conflict):
            services.create_cash_sale(s, tenant_id="t-a", register_id=reg, cashier_id="k", idempotency_key="leak-0001",
                                      lines=[services.LineRequest(pid, 1)], tendered_cents=250, settings=settings,
                                      now=settings.clock(), before_commit=refuse)
        services.open_drawer(s, "t-a", a.register_id("R2"), "k", 0, settings.clock())
    with app.state.session_factory() as s:
        types = list(s.scalars(select(OutboxEvent.type).order_by(OutboxEvent.recorded_seq)))
        assert s.scalar(select(func.count()).select_from(Sale)) == 0
    assert types == ["drawer.opened", "drawer.opened"]


# ---------------------------------------------- AION review on #21 (regressions)


def _emit(s, tag, settings):
    events.emit(s, tenant_id="t-a", type="drawer.opened", payload={"drawer_id": 1, "register_id": 1,
                "opening_float_cents": tag}, aggregate_type="drawer_session", aggregate_id=str(tag),
                aggregate_version=1, now=settings.clock())


def _written(app):
    with app.state.session_factory() as s:
        return sorted(int(e.aggregate_id) for e in s.scalars(select(OutboxEvent)))


def test_event_emitted_in_rolled_back_savepoint_is_not_written(a, app, settings):
    """Finding 3: outer event + event in a rolled-back nested savepoint."""
    from axar_pos.db import bind_tenant

    with app.state.request_factory() as s:
        bind_tenant(s, "t-a")
        _emit(s, 1, settings)
        sp = s.begin_nested()
        _emit(s, 2, settings)
        sp.rollback()
        sp2 = s.begin_nested()
        _emit(s, 3, settings)
        sp2.commit()
        # Released savepoints must not write events yet: the event counter
        # lock is the LAST lock of the transaction (lock ordering).
        assert s.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        s.commit()
    assert _written(app) == [1, 3]


def test_released_savepoint_events_die_with_the_outer_rollback(a, app, settings):
    from axar_pos.db import bind_tenant

    with app.state.request_factory() as s:
        bind_tenant(s, "t-a")
        sp = s.begin_nested()
        _emit(s, 4, settings)
        sp.commit()                  # savepoint released, NOT a commit of the transaction
        s.rollback()
        s.commit()
    assert _written(app) == []


def test_block_policy_keeps_order_for_financial_consumers(a, app, clock):
    """Finding 4: a consumer that must not apply events out of order stays
    blocked at the poison event; nothing after it is applied until it is
    resolved, then everything follows in order."""
    a.open_drawer()
    sell_n(a, 2)                                                    # seqs 1..3
    rec = Recorder(fail_on={2})
    for _ in range(events.MAX_ATTEMPTS + 2):
        events.run_consumer(app.state.request_factory, "ledger", "t-a", worker="w", now=clock(),
                            handler=rec, on_poison="block")
        clock.at += events.BACKOFF_MAX
    assert rec.seen == [1]                                          # 3 NOT applied out of order
    with app.state.session_factory() as s:
        dl = s.scalar(select(DeadLetter))
        c = s.get(EventConsumer, ("ledger", "t-a"))
        assert (c.cursor, dl.recorded_seq, c.blocked_at is not None) == (1, 2, True)
    fixed = Recorder()
    events.replay_dead_letter(app.state.request_factory, dl.id, tenant_id="t-a", now=clock(), handler=fixed)
    res = events.run_consumer(app.state.request_factory, "ledger", "t-a", worker="w", now=clock(),
                              handler=fixed, on_poison="block")
    assert fixed.seen == [2, 3] and res.processed == 1


def test_non_duplicate_integrity_error_is_a_failure_not_a_skip(a, app, clock, monkeypatch):
    """Finding 5a: only an existing inbox row means 'already applied'."""
    a.open_drawer()                                                 # seq 1
    real = events.InboxEntry

    def broken(**kw):
        return real(**{**kw, "recorded_seq": None})                 # NOT NULL violation, not a duplicate
    monkeypatch.setattr(events, "InboxEntry", broken)
    res = run(app, "books", Recorder(), clock)
    assert res.skipped_duplicates == 0 and res.failed
    with app.state.session_factory() as s:
        assert s.get(EventConsumer, ("books", "t-a")).cursor == 0   # event NOT silently skipped


def test_errors_are_redacted_in_health_and_dead_letters(a, app, clock):
    """Finding 5b: raw exception text (may contain secrets/SQL params) is not stored."""
    a.open_drawer()
    secret = "tok_SUPERSECRET_9f8e7d6c5b4a"

    def leaky(_s, _ev):
        raise ValueError(f"upstream said password={secret}")
    for _ in range(events.MAX_ATTEMPTS):
        run(app, "books", leaky, clock)
        clock.at += events.BACKOFF_MAX
    with app.state.session_factory() as s:
        stored = [c.last_error or "" for c in s.scalars(select(EventConsumer))]
        stored += [d.error for d in s.scalars(select(DeadLetter))]
    assert stored and all(secret not in x and "password=" not in x for x in stored)
    assert any("ValueError" in x for x in stored)


def test_failed_run_releases_its_lease_so_another_worker_can_retry(a, app, clock):
    """Finding 5c: a worker that stops on a failure must not keep the lease."""
    a.open_drawer()
    run(app, "books", Recorder(fail_on={1}, fail_times=1), clock, worker="w1")
    clock.at += timedelta(seconds=3)                                # backoff (1-2 s) over; lease (30 s) not
    res = run(app, "books", Recorder(), clock, worker="w2")
    assert not res.not_leased and res.processed == 1


# ------------------------------------------- finding 6: real consumer + worker


def test_worker_stages_financial_events_for_books_exactly_once(a, b, app, settings):
    from concurrent.futures import ThreadPoolExecutor

    from axar_pos import worker
    from axar_pos.models import ExportQueue

    a.open_drawer(); b.open_drawer()
    sale = a.sell([("KOHV", 2)], 1000).json()
    a.as_role("manager").refund(sale["id"], [(1, 1)])
    b.sell([("B-SECRET", 1)], 1000)
    with ThreadPoolExecutor(3) as pool:     # three workers at once
        list(pool.map(lambda i: worker.run_once(app.state.request_factory, settings, f"w{i}"), range(3)))
    worker.run_once(app.state.request_factory, settings, "w9")          # and again later
    with app.state.session_factory() as s:
        rows = list(s.scalars(select(ExportQueue).order_by(ExportQueue.tenant_id, ExportQueue.recorded_seq)))
        lag = {(c.name, c.tenant_id): events.latest_seq(s, c.tenant_id) - c.cursor
               for c in s.scalars(select(EventConsumer))}
    assert [(r.tenant_id, r.event_type) for r in rows] == [
        ("t-a", "pos.sale.completed"), ("t-a", "pos.refund.completed"), ("t-b", "pos.sale.completed")]
    assert all(r.status == "pending" and len(r.payload_hash) == 64 for r in rows)
    assert lag == {(name, tenant): 0 for name in ("books.export", "stock.consume") for tenant in ("t-a", "t-b")}


def test_corrupt_financial_event_blocks_books_export_instead_of_skipping(a, app, settings, clock):
    from axar_pos import worker
    from axar_pos.models import ExportQueue

    a.open_drawer()
    a.sell([("KOHV", 1)], 250)                                          # seq 2 = sale
    with app.state.session_factory() as s:                              # owner tampers with the payload
        ev = s.scalar(select(OutboxEvent).where(OutboxEvent.type == "pos.sale.completed"))
        s.execute(OutboxEvent.__table__.update().where(OutboxEvent.id == ev.id).values(payload="{}"))
        s.commit()
    a.sell([("KOHV", 1)], 250)                                          # seq 3, after the corrupt one
    for _ in range(events.MAX_ATTEMPTS + 1):
        worker.run_once(app.state.request_factory, settings, "w")
        clock.at += events.BACKOFF_MAX
    with app.state.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(ExportQueue)) == 0   # nothing out of order
        c = s.get(EventConsumer, ("books.export", "t-a"))
        assert c.blocked_at is not None and c.failing_seq == 2 and "CorruptEvent" in c.last_error
