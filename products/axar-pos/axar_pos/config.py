from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Settings:
    database_url: str = "sqlite:///./axar_pos.db"
    # Owner connection for migrations and seeding. On PostgreSQL the app's own
    # ``database_url`` should be the non-owner role ``axar_app`` (RLS applies).
    admin_database_url: str | None = None
    business_timezone: str = "Europe/Tallinn"
    # Hour (local time) at which a business day starts. 0 = midnight. A bar
    # closing at 04:00 would use 5 so late sales count for the previous day.
    business_day_cutoff_hour: int = 0
    cash_rounding_step_cents: int = 5
    # Demo identity: tenant/cashier taken from request headers with NO
    # authentication. Must be switched on explicitly; when off, every
    # tenant-scoped endpoint refuses to serve (issue #2: no implicit demo
    # identity in production).
    demo_identity: bool = False
    seed_demo_data: bool = False
    # Server auth (RFC-0002). 32 bytes, urlsafe base64; encrypts TOTP seeds.
    # Never commit it; set via the environment / secret store.
    secret_key: str | None = None
    # "dev" (default) or "pilot". The pilot profile refuses to start with
    # anything that is only acceptable on a developer machine (G-SEC-2).
    profile: str = "dev"
    # Run the event consumers inside the API process (single-process setups
    # and demos). Several processes are safe: consumers are lease-based.
    inprocess_worker: bool = False
    till_idle_minutes: int = 15
    session_absolute_hours: int = 12
    admin_idle_minutes: int = 15
    admin_absolute_hours: int = 8
    # Realtime feed: DB poll interval per open stream and session re-check.
    stream_poll_s: float = 0.5
    stream_auth_recheck_s: float = 15.0
    clock: Callable[[], datetime] = field(default=utc_now)

    def pilot_problems(self) -> list[str]:
        """Reasons this configuration must not run as a pilot (empty = ok)."""
        problems = []
        if self.demo_identity:
            problems.append("demo identity (unauthenticated headers) is enabled")
        if not self.database_url.startswith("postgresql"):
            problems.append("database is not PostgreSQL")
        if not self.admin_database_url or self.admin_database_url == self.database_url:
            problems.append("app and owner database connections are the same (RLS would not apply)")
        if not self.secret_key:
            problems.append("AXAR_POS_SECRET_KEY is not set")
        return problems

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("AXAR_POS_DATABASE_URL", cls.database_url),
            admin_database_url=os.environ.get("AXAR_POS_ADMIN_DATABASE_URL") or None,
            business_timezone=os.environ.get("AXAR_POS_TIMEZONE", cls.business_timezone),
            business_day_cutoff_hour=int(os.environ.get("AXAR_POS_DAY_CUTOFF_HOUR", "0")),
            cash_rounding_step_cents=int(os.environ.get("AXAR_POS_CASH_ROUNDING", "5")),
            demo_identity=os.environ.get("AXAR_POS_DEMO_IDENTITY") == "1",
            seed_demo_data=os.environ.get("AXAR_POS_SEED_DEMO") == "1",
            secret_key=os.environ.get("AXAR_POS_SECRET_KEY") or None,
            profile=os.environ.get("AXAR_POS_PROFILE", "dev"),
            inprocess_worker=os.environ.get("AXAR_POS_INPROCESS_WORKER") == "1",
            till_idle_minutes=int(os.environ.get("AXAR_POS_TILL_IDLE_MINUTES", "15")),
        )
