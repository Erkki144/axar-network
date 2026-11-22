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
    clock: Callable[[], datetime] = field(default=utc_now)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("AXAR_POS_DATABASE_URL", cls.database_url),
            business_timezone=os.environ.get("AXAR_POS_TIMEZONE", cls.business_timezone),
            business_day_cutoff_hour=int(os.environ.get("AXAR_POS_DAY_CUTOFF_HOUR", "0")),
            cash_rounding_step_cents=int(os.environ.get("AXAR_POS_CASH_ROUNDING", "5")),
            demo_identity=os.environ.get("AXAR_POS_DEMO_IDENTITY") == "1",
            seed_demo_data=os.environ.get("AXAR_POS_SEED_DEMO") == "1",
        )
