"""SQLite-backed *staging* for future bookkeeping adapters; no network dispatch."""
import hashlib
import json
import sqlite3
from pathlib import Path


class OutboxConflict(ValueError):
    pass


class SQLiteOutbox:
    """Local crash-resistant idempotent queue, NOT a distributed worker.

    Future connector must verify provider receipt before calling acknowledge().
    Authentication, scopes, webhooks, credentials and provider retries are out
    of scope: this package cannot transfer funds or submit journal entries.
    """

    def __init__(self, filename: str | Path):
        self.filename = str(filename)
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS exports (
                tenant_id TEXT NOT NULL, destination TEXT NOT NULL,
                event_id TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', ack TEXT,
                PRIMARY KEY (tenant_id, destination, event_id)
            )""")

    def _conn(self):
        c = sqlite3.connect(self.filename, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def stage(self, tenant_id: str, destination: str, event_id: str, document: dict):
        if not all(isinstance(s, str) and s.strip() for s in (tenant_id,destination,event_id)):
            raise ValueError("tenant/destination/event ID required")
        if not isinstance(document, dict) or document.get("tenant_id") != tenant_id:
            raise OutboxConflict("document tenant mismatch")
        body = json.dumps(document, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute("SELECT digest FROM exports WHERE tenant_id=? AND destination=? AND event_id=?",
                                 (tenant_id,destination,event_id)).fetchone()
            if existing and existing["digest"] != digest:
                raise OutboxConflict("event ID reused with different document")
            if not existing:
                c.execute("INSERT INTO exports (tenant_id,destination,event_id,body,digest) VALUES (?,?,?,?,?)",
                          (tenant_id,destination,event_id,body,digest))
        return self.get(tenant_id,destination,event_id)

    def get(self, tenant_id, destination, event_id):
        with self._conn() as c:
            row=c.execute("SELECT * FROM exports WHERE tenant_id=? AND destination=? AND event_id=?",
                          (tenant_id,destination,event_id)).fetchone()
            if row is None:
                raise KeyError("export not found for tenant/destination")
            return dict(row)

    def pending(self, tenant_id: str, destination: str):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM exports WHERE tenant_id=? AND destination=? AND status='pending' ORDER BY event_id LIMIT 100",
                (tenant_id,destination)).fetchall()]

    def acknowledge(self, tenant_id, destination, event_id, expected_digest, verified_provider_receipt):
        if not isinstance(verified_provider_receipt, str) or not verified_provider_receipt.strip():
            raise ValueError("verified provider receipt required")
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT digest,status,ack FROM exports WHERE tenant_id=? AND destination=? AND event_id=?",
                          (tenant_id,destination,event_id)).fetchone()
            if not row:
                raise KeyError("export not found")
            if row["digest"] != expected_digest:
                raise OutboxConflict("payload hash mismatch")
            if row["status"] == "acknowledged" and row["ack"] != verified_provider_receipt:
                raise OutboxConflict("provider acknowledgement already fixed")
            c.execute("UPDATE exports SET status='acknowledged',ack=? WHERE tenant_id=? AND destination=? AND event_id=?",
                      (verified_provider_receipt,tenant_id,destination,event_id))
        return self.get(tenant_id,destination,event_id)
