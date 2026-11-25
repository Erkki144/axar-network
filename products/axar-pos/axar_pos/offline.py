"""Offline limit (ADR-0012 §6, owner decision 2026-11-25): a till may sell
without the central server for at most ``offline_limit_hours`` (1-72, set by a
manager in the POS settings; default and maximum 72).

- CENTRAL keeps the setting (``pos_settings``) and returns it in every sync
  answer, so the till always works with the current value.
- EDGE records each successful contact (``edge_sync_state``). When nothing is
  pending, the sync agent still checks in, which renews the contact.
- Every new sale or refund on the till goes through ``guard``: past the limit,
  or with the clock set back, it is refused. Nothing already sold is touched;
  the queue syncs as soon as the connection is back, and that contact lifts
  the block.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from . import auth
from .models import EdgeSyncState, PosSettings
from .services import DomainError

MAX_OFFLINE_HOURS = 72
DEFAULT_OFFLINE_HOURS = 72
CLOCK_TOLERANCE = timedelta(minutes=5)


class OfflineLimitExceeded(DomainError):
    status_code = 423
    code = "offline_limit_exceeded"


class ClockMovedBack(DomainError):
    status_code = 423
    code = "clock_moved_back"


# ------------------------------------------------------------------ central


def offline_limit_hours(s: Session, tenant_id: str) -> int:
    row = s.get(PosSettings, tenant_id)
    return row.offline_limit_hours if row else DEFAULT_OFFLINE_HOURS


def set_offline_limit(s: Session, *, tenant_id: str, hours: int, actor: str, now: datetime) -> int:
    if not isinstance(hours, int) or not 1 <= hours <= MAX_OFFLINE_HOURS:
        raise DomainError(f"offline limit must be a whole number of hours from 1 to {MAX_OFFLINE_HOURS}")
    row = s.get(PosSettings, tenant_id)
    before = row.offline_limit_hours if row else DEFAULT_OFFLINE_HOURS
    if row is None:
        s.add(PosSettings(tenant_id=tenant_id, offline_limit_hours=hours, updated_at=now, updated_by=actor))
    else:
        row.offline_limit_hours, row.updated_at, row.updated_by = hours, now, actor
    auth.audit(s, now, "pos_settings.changed", "ok", tenant_id=tenant_id, staff_id=actor,
               detail=json.dumps({"offline_limit_hours": {"before": before, "after": hours}}))
    s.commit()
    return hours


# --------------------------------------------------------------------- edge


def record_contact(s: Session, *, tenant_id: str, device_id: str, now: datetime, limit_hours: int) -> None:
    """A successful exchange with the central server (in its own transaction)."""
    limit_hours = max(1, min(int(limit_hours), MAX_OFFLINE_HOURS))   # never trust more than the policy maximum
    ins = (postgresql.insert if s.get_bind().dialect.name == "postgresql" else sqlite.insert)(EdgeSyncState)
    s.execute(ins.values(tenant_id=tenant_id, device_id=device_id, last_contact_at=now, max_seen_at=now,
                         offline_limit_hours=limit_hours).on_conflict_do_nothing())
    st = s.scalar(select(EdgeSyncState).where(EdgeSyncState.tenant_id == tenant_id,
                                              EdgeSyncState.device_id == device_id)
                  .with_for_update().execution_options(populate_existing=True))
    st.last_contact_at = max(st.last_contact_at, now)
    st.max_seen_at = max(st.max_seen_at, now)
    st.offline_limit_hours = limit_hours
    s.commit()


def status(s: Session, *, tenant_id: str, device_id: str, now: datetime) -> dict:
    st = s.get(EdgeSyncState, (tenant_id, device_id))
    if st is None:
        return {"connected_once": False, "blocked": True, "reason": "never_connected",
                "last_contact_at": None, "limit_hours": None, "deadline": None, "remaining_s": 0}
    deadline = st.last_contact_at + timedelta(hours=st.offline_limit_hours)
    reason = ("clock_moved_back" if now < st.max_seen_at - CLOCK_TOLERANCE
              else "offline_limit_exceeded" if now > deadline else None)
    return {"connected_once": True, "blocked": reason is not None, "reason": reason,
            "last_contact_at": st.last_contact_at.isoformat(), "limit_hours": st.offline_limit_hours,
            "deadline": deadline.isoformat(), "remaining_s": max(0, int((deadline - now).total_seconds()))}


def guard(s: Session, settings, tenant_id: str, now: datetime) -> None:
    """Call before a NEW money operation on an edge node (no-op centrally).
    Runs inside the operation's transaction; it also advances ``max_seen_at``."""
    if not settings.edge_device_id:
        return
    st = s.scalar(select(EdgeSyncState).where(EdgeSyncState.tenant_id == tenant_id,
                                              EdgeSyncState.device_id == settings.edge_device_id))
    if st is None:
        raise OfflineLimitExceeded("this till has not connected to the central server yet; "
                                   "connect it once before selling")
    if now < st.max_seen_at - CLOCK_TOLERANCE:
        raise ClockMovedBack("the till's clock is behind the last time it used; fix the clock "
                             "(selling is blocked so the offline limit cannot be bypassed)")
    deadline = st.last_contact_at + timedelta(hours=st.offline_limit_hours)
    if now > deadline:
        raise OfflineLimitExceeded(f"no contact with the central server for more than "
                                   f"{st.offline_limit_hours} h; reconnect to continue selling "
                                   f"(sales made so far are kept and will sync)")
    if now > st.max_seen_at:
        st.max_seen_at = now
