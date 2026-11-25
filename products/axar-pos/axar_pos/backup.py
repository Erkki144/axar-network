"""PostgreSQL backup, restore and restore verification (SEC-2, #34).

    python -m axar_pos.backup dump    --out /backups/axar-20261125.dump
    python -m axar_pos.backup restore --dump /backups/axar-20261125.dump   # into an EMPTY database
    python -m axar_pos.backup verify  --dump /backups/axar-20261125.dump   # restored == backed up?

All three use the OWNER (admin) connection from ``AXAR_POS_ADMIN_DATABASE_URL``
(the target database for restore/verify). Credentials go to ``pg_dump`` /
``pg_restore`` through libpq environment variables, never on the command line
(where any local user could read them with ``ps``).

A dump is three files: ``X.dump`` (pg_dump custom format, includes RLS policies
and grants), ``X.dump.sha256`` and ``X.dump.manifest.json`` (alembic revision +
a content fingerprint of every table). ``restore`` refuses a dump whose
checksum does not match, and a target that is not empty. ``verify`` proves the
restored database has exactly the same content as the original at backup time.

Encryption of backups at rest and off-site copies are the storage's job (see
docs/OPERATIONS_BACKUP.md); this module never uploads anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

APP_ROLE = "axar_app"


class BackupError(RuntimeError):
    pass


def _libpq_env(sqlalchemy_url: str) -> dict[str, str]:
    u = make_url(sqlalchemy_url)
    if not u.drivername.startswith("postgresql"):
        raise BackupError("backup/restore is for PostgreSQL deployments")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    for key, val in (("PGHOST", u.host), ("PGPORT", u.port), ("PGUSER", u.username),
                     ("PGPASSWORD", u.password), ("PGDATABASE", u.database)):
        if val is not None:
            env[key] = str(val)
    return env


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(admin_url: str) -> dict:
    """Row count + content hash of every table in ``public`` (owner connection:
    the table owner is not subject to RLS, so all tenants are covered)."""
    eng = create_engine(admin_url)
    try:
        with eng.connect() as c:
            tables = list(c.scalars(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")))
            out = {}
            for t in tables:
                n, digest = c.execute(text(
                    f'SELECT count(*), md5(coalesce(string_agg(x::text, E\'\\n\' ORDER BY x::text), \'\')) '
                    f'FROM public."{t}" x')).one()
                out[t] = {"rows": int(n), "md5": digest}
            rev = c.scalar(text("SELECT version_num FROM alembic_version")) if "alembic_version" in tables else None
            return {"alembic_revision": rev, "tables": out}
    finally:
        eng.dispose()


def dump(admin_url: str, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    fp = fingerprint(admin_url)
    proc = subprocess.run(["pg_dump", "--format=custom", "--compress=6", "--file", str(out)],
                          env=_libpq_env(admin_url), capture_output=True, text=True)
    if proc.returncode != 0:
        raise BackupError(f"pg_dump failed: {proc.stderr.strip()[:500]}")
    # Content must not have changed while dumping (pg_dump is one snapshot;
    # the fingerprint is another): a mismatch means writes happened in between.
    if fingerprint(admin_url) != fp:
        out.unlink(missing_ok=True)
        raise BackupError("database changed during backup; run again (or dump from a quiesced/standby server)")
    digest = _sha256(out)
    Path(f"{out}.sha256").write_text(f"{digest}  {out.name}\n")
    manifest = {"format": "axar-pos-backup/1", "created_at": datetime.now(timezone.utc).isoformat(),
                "sha256": digest, **fp}
    Path(f"{out}.manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return manifest


def _manifest(dump_path: Path) -> dict:
    return json.loads(Path(f"{dump_path}.manifest.json").read_text())


def restore(admin_url: str, dump_path: Path) -> None:
    manifest = _manifest(dump_path)
    if _sha256(dump_path) != manifest["sha256"]:
        raise BackupError("dump checksum mismatch: file is corrupted or was altered; refusing to restore")
    eng = create_engine(admin_url)
    try:
        with eng.connect() as c:
            if c.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")):
                raise BackupError("target database is not empty; restore only into a new, empty database")
            if c.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}) is None:
                raise BackupError(f"role {APP_ROLE} does not exist on the target server; create it first "
                                  "(roles are per server, not part of a database dump)")
    finally:
        eng.dispose()
    proc = subprocess.run(["pg_restore", "--exit-on-error", "--single-transaction", "--no-owner",
                           "--dbname", make_url(admin_url).database, str(dump_path)],
                          env=_libpq_env(admin_url), capture_output=True, text=True)
    if proc.returncode != 0:
        raise BackupError(f"pg_restore failed (nothing was restored): {proc.stderr.strip()[:500]}")


def verify(admin_url: str, dump_path: Path) -> list[str]:
    """Differences between the restored database and the manifest (empty = identical)."""
    expected = _manifest(dump_path)
    actual = fingerprint(admin_url)
    diffs = []
    if actual["alembic_revision"] != expected["alembic_revision"]:
        diffs.append(f"alembic revision {actual['alembic_revision']} != {expected['alembic_revision']}")
    for t in sorted(set(expected["tables"]) | set(actual["tables"])):
        if expected["tables"].get(t) != actual["tables"].get(t):
            diffs.append(f"{t}: {actual['tables'].get(t)} != {expected['tables'].get(t)}")
    return diffs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m axar_pos.backup")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dump").add_argument("--out", required=True, type=Path)
    sub.add_parser("restore").add_argument("--dump", required=True, type=Path)
    sub.add_parser("verify").add_argument("--dump", required=True, type=Path)
    args = p.parse_args(argv)
    url = os.environ.get("AXAR_POS_ADMIN_DATABASE_URL")
    if not url:
        print("set AXAR_POS_ADMIN_DATABASE_URL (owner connection)", file=sys.stderr)
        return 2
    try:
        if args.cmd == "dump":
            m = dump(url, args.out)
            print(f"backup written: {args.out} sha256={m['sha256']} revision={m['alembic_revision']}")
        elif args.cmd == "restore":
            restore(url, args.dump)
            print("restore completed; run `verify` next")
        else:
            diffs = verify(url, args.dump)
            if diffs:
                print("VERIFY FAILED:\n" + "\n".join(diffs), file=sys.stderr)
                return 1
            print("verify OK: restored content is identical to the backup")
    except BackupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
