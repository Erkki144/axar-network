from concurrent.futures import ThreadPoolExecutor
import pytest
from axar_connect.outbox import SQLiteOutbox,OutboxConflict

@pytest.fixture
def q(tmp_path):
    return SQLiteOutbox(tmp_path/"outbox.sqlite")


def doc(tenant="t-a",value=700):
    return {"tenant_id":tenant,"journal_id":"J","postings":[{"debit_cents":value}]}


def test_idempotent_staging(q):
    first=q.stage("t-a","merit-mock","evt",doc())
    again=q.stage("t-a","merit-mock","evt",doc())
    assert first==again and len(q.pending("t-a","merit-mock"))==1


def test_conflicting_reuse_rejected(q):
    q.stage("t-a","merit-mock","evt",doc())
    with pytest.raises(OutboxConflict):
        q.stage("t-a","merit-mock","evt",doc(value=701))


def test_tenant_mismatch_rejected(q):
    with pytest.raises(OutboxConflict):
        q.stage("t-a","merit-mock","evt",doc(tenant="t-b"))


def test_isolation_of_tenant_and_destination(q):
    q.stage("t-a","merit-mock","evt",doc())
    assert not q.pending("t-b","merit-mock")
    assert not q.pending("t-a","directo-mock")
    with pytest.raises(KeyError):
        q.get("t-b","merit-mock","evt")
    q.stage("t-b","merit-mock","evt",doc(tenant="t-b"))
    q.stage("t-a","directo-mock","evt",doc())
    assert len(q.pending("t-a","directo-mock"))==1


def test_verified_ack_required(q):
    e=q.stage("t-a","merit-mock","evt",doc())
    with pytest.raises(ValueError):
        q.acknowledge("t-a","merit-mock","evt",e["digest"],"")
    with pytest.raises(OutboxConflict):
        q.acknowledge("t-a","merit-mock","evt","wrong","ack-1")
    assert q.pending("t-a","merit-mock")
    r=q.acknowledge("t-a","merit-mock","evt",e["digest"],"ack-1")
    assert r["status"]=="acknowledged" and not q.pending("t-a","merit-mock")
    assert q.acknowledge("t-a","merit-mock","evt",e["digest"],"ack-1")==r
    with pytest.raises(OutboxConflict):
        q.acknowledge("t-a","merit-mock","evt",e["digest"],"different")


def test_wrong_tenant_cannot_ack(q):
    e=q.stage("t-a","merit-mock","evt",doc())
    with pytest.raises(KeyError):
        q.acknowledge("t-b","merit-mock","evt",e["digest"],"ack")


def test_persists_after_restarting_process(tmp_path):
    path=tmp_path/"q.db"
    SQLiteOutbox(path).stage("t-a","merit-mock","evt",doc())
    assert len(SQLiteOutbox(path).pending("t-a","merit-mock"))==1


def test_concurrent_replays_single_row(q):
    def worker(_):
        return q.stage("t-a","merit-mock","evt",doc())["digest"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        hashes=list(pool.map(worker,range(16)))
    assert len(set(hashes))==1
    assert len(q.pending("t-a","merit-mock"))==1


def test_no_network_access_required(q,monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,"connect",lambda *_: (_ for _ in ()).throw(AssertionError("network forbidden")))
    e=q.stage("t-a","merit-mock","evt",doc())
    q.acknowledge("t-a","merit-mock","evt",e["digest"],"mock-ack")
