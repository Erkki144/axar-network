"""Consumer worker: ``python -m axar_pos.worker`` (loop) or ``--once``.

Runs every registered consumer for every tenant. Safe to start several
copies: each (consumer, tenant) is processed only by its lease holder, and a
worker that loses its lease cannot move the cursor (fencing token).
Connects as the app role; each run binds its tenant (RLS applies).
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import time

from sqlalchemy import select

from . import consumers  # noqa: F401  (registers the handlers)
from .config import Settings
from .db import init_db, make_engine, request_factory
from .events import CONSUMERS, RunResult, run_consumer
from .models import Tenant

log = logging.getLogger("axar.worker")


def run_once(factory, settings: Settings, worker_id: str) -> dict[tuple[str, str], RunResult]:
    with factory() as s:
        tenants = list(s.scalars(select(Tenant.id)))
    out = {}
    for tenant in tenants:
        for name in sorted(CONSUMERS):
            res = run_consumer(factory, name, tenant, worker=worker_id, now=settings.clock())
            out[(name, tenant)] = res
            if res.blocked or res.failed or res.dead_lettered:
                log.warning("consumer %s tenant %s: blocked=%s failed=%s dead_lettered=%s",
                            name, tenant, res.blocked, res.failed, res.dead_lettered)
    return out


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="axar_pos.worker")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=float, default=2.0)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    init_db(make_engine(settings.admin_database_url or settings.database_url))  # migrations (owner)
    factory = request_factory(make_engine(settings.database_url))
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    while True:
        run_once(factory, settings, worker_id)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
