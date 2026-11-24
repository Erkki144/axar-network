"""Server-side identity and authorization (issue #2, RFC-0002).

Model:
- A device (till/tablet) is ENROLLED once with a single-use code created by a
  manager/owner. It receives a random device token; only its SHA-256 is stored.
- Staff unlock a till with a PIN that is valid only on an enrolled device of
  their tenant and location. PINs are argon2id hashes; repeated failures lock
  the staff member AND the device with exponential backoff.
- Managers/owners log in to the back office with password (argon2id) + TOTP
  (RFC 6238). Only such MFA sessions may administer staff and devices.
- Sessions are opaque 256-bit bearer tokens, stored as SHA-256, checked on
  every request (idle + absolute expiry), revoked at once on logout, staff
  deactivation or device revocation. Till sessions are bound to their device:
  the device token must accompany every request.
- Refunds and non-zero drawer variances need a step-up approval: a DIFFERENT
  manager/owner enters their PIN on the same device; the approval is single
  use, short-lived and bound to the exact request (hash).
- Roles come only from the database. Request headers never grant anything.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    Approval,
    AuthEvent,
    AuthSession,
    Device,
    EnrollmentCode,
    Location,
    Register,
    Staff,
    StaffRole,
    Tenant,
)
from .services import Conflict, DomainError, Forbidden, NotFound

ROLES = ("cashier", "waiter", "kitchen", "manager", "owner")
MANAGER_ROLES = ("manager", "owner")

# Permission -> roles allowed. Scope (tenant / location / register) is checked
# separately by the caller.
PERMISSIONS: dict[str, tuple[str, ...]] = {
    "catalog.read": ROLES,
    "sale": ("cashier", "waiter", "manager", "owner"),
    "sales.read": ("cashier", "waiter", "manager", "owner"),
    "drawer": ("cashier", "manager", "owner"),
    "refund.request": ("cashier", "manager", "owner"),
    "reports": MANAGER_ROLES,
    "orders": ("waiter", "cashier", "manager", "owner"),
    "kitchen.read": ROLES,
    "kitchen.update": ("kitchen", "manager", "owner"),
    "admin": MANAGER_ROLES,
}

PIN_MIN, PIN_MAX = 4, 8
PASSWORD_MIN = 12
LOCK_AFTER = 5                      # failures before backoff starts
LOCK_BASE = timedelta(seconds=30)   # 30 s, 60 s, 120 s ... capped
LOCK_MAX = timedelta(hours=1)
APPROVAL_TTL = timedelta(seconds=60)
ENROLL_TTL = timedelta(minutes=15)
TOUCH_EVERY = timedelta(seconds=30)  # last_seen write throttle

_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)  # OWASP argon2id
_DUMMY_HASH = _hasher.hash("timing-equalizer-not-a-secret")


# ------------------------------------------------------------------ errors


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


class InvalidCredentials(Unauthorized):
    code = "invalid_credentials"


class Locked(DomainError):
    status_code = 429
    code = "locked"


class DeviceRequired(Forbidden):
    code = "device_required"


class MfaRequired(Forbidden):
    code = "mfa_required"


class InvalidApproval(Forbidden):
    code = "approval_invalid"


class WeakSecret(DomainError):
    status_code = 422
    code = "weak_secret"


# ---------------------------------------------------------- primitives


def token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_secret(value: str) -> str:
    return _hasher.hash(value)


def verify_secret(stored: str | None, value: str) -> bool:
    """Constant work whether or not a hash exists (no user enumeration)."""
    try:
        return _hasher.verify(stored or _DUMMY_HASH, value) and stored is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def check_pin(pin: str) -> None:
    if not (pin.isdigit() and PIN_MIN <= len(pin) <= PIN_MAX):
        raise WeakSecret(f"PIN must be {PIN_MIN}-{PIN_MAX} digits")
    if len(set(pin)) == 1 or pin in "0123456789012" or pin in "9876543210987":
        raise WeakSecret("PIN is too easy to guess")


def check_password(pw: str) -> None:
    if len(pw) < PASSWORD_MIN or len(pw) > 200:
        raise WeakSecret(f"password must be {PASSWORD_MIN}-200 characters")


# ----------------------------------------------------------------- TOTP


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret_b32: str, step: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_match(secret_b32: str, code: str, now: datetime, last_step: int | None) -> int | None:
    """Matching time step within ±1 (30 s) that is newer than the last used
    one (replay protection), else None."""
    if not (code.isdigit() and len(code) == 6):
        return None
    current = int(now.timestamp()) // 30
    for step in (current - 1, current, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp_at(secret_b32, step), code):
            return step
    return None


def provisioning_uri(secret_b32: str, account: str, issuer: str = "AXAR POS") -> str:
    from urllib.parse import quote

    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret_b32}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30")


class SecretBox:
    """AES-256-GCM for secrets that must be recoverable (TOTP seeds)."""

    def __init__(self, key_b64: str):
        key = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
        if len(key) != 32:
            raise ValueError("AXAR_POS_SECRET_KEY must be 32 bytes, urlsafe base64")
        self._aes = AESGCM(key)

    def seal(self, plaintext: str, aad: str) -> str:
        nonce = os.urandom(12)
        ct = self._aes.encrypt(nonce, plaintext.encode(), aad.encode())
        return "v1:" + base64.urlsafe_b64encode(nonce + ct).decode()

    def open(self, sealed: str, aad: str) -> str:
        if not sealed.startswith("v1:"):
            raise ValueError("unknown secret format")
        raw = base64.urlsafe_b64decode(sealed[3:])
        return self._aes.decrypt(raw[:12], raw[12:], aad.encode()).decode()


# ------------------------------------------------------------- throttling


class RateLimiter:
    """In-process sliding window for unauthenticated endpoints (enrol,
    password login). A per-process limit; the per-account lockouts in the
    database are the durable defence."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            hits.append(now)
            self._hits[key] = hits
            return len(hits) <= self.limit


def _backoff(failures: int) -> timedelta:
    over = failures - LOCK_AFTER
    return min(LOCK_BASE * (2 ** over), LOCK_MAX)


# ---------------------------------------------------------------- identity


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    tenant_name: str
    staff_id: str
    staff_name: str
    roles: frozenset[str]           # roles valid in this session's scope
    session_id: str | None = None
    device_id: str | None = None
    location_id: int | None = None
    register_id: int | None = None
    kind: str = "demo"              # demo | till | admin
    mfa: bool = False
    demo: bool = False
    location_ids: frozenset[int | None] = field(default_factory=frozenset)  # admin scope

    @property
    def cashier_id(self) -> str:   # name used by the existing services
        return self.staff_id

    @property
    def role(self) -> str:         # strongest role, for display/legacy checks
        for r in ("owner", "manager", "cashier", "waiter", "kitchen"):
            if r in self.roles:
                return r
        return "none"

    def can(self, permission: str) -> bool:
        return self.demo or bool(self.roles & set(PERMISSIONS[permission]))


def audit(s: Session, now: datetime, event: str, outcome: str, *, tenant_id=None, staff_id=None,
          device_id=None, session_id=None, ip=None, detail=None) -> None:
    s.add(AuthEvent(tenant_id=tenant_id, at=now, event=event, outcome=outcome, staff_id=staff_id,
                    device_id=device_id, session_id=session_id, ip=(ip or "")[:64] or None,
                    detail=(detail or "")[:300] or None))


def roles_at(staff: Staff, location_id: int | None) -> frozenset[str]:
    """Roles that apply at a location (tenant-wide roles apply everywhere)."""
    return frozenset(r.role for r in staff.roles if r.location_id is None or r.location_id == location_id)


def device_by_token(s: Session, raw: str | None) -> Device | None:
    if not raw or len(raw) > 200:
        return None
    d = s.scalar(select(Device).where(Device.token_hash == token_hash(raw)))
    return d if d is not None and d.revoked_at is None else None


def _new_session(s: Session, *, staff: Staff, device: Device | None, kind: str, mfa: bool,
                 settings: Settings, now: datetime) -> tuple[AuthSession, str]:
    raw = token()
    absolute = timedelta(hours=settings.session_absolute_hours if kind == "till" else settings.admin_absolute_hours)
    sess = AuthSession(id=str(uuid.uuid4()), tenant_id=staff.tenant_id, token_hash=token_hash(raw),
                       staff_id=staff.id, device_id=device.id if device else None, kind=kind, mfa=mfa,
                       created_at=now, last_seen_at=now, expires_at=now + absolute)
    s.add(sess)
    return sess, raw


def resolve_session(s: Session, *, bearer: str | None, device_token: str | None,
                    settings: Settings, now: datetime) -> Principal:
    """Bearer session -> Principal, or Unauthorized. Checks expiry, idle time,
    revocation, staff active, device binding and device revocation."""
    if not bearer or len(bearer) > 200:
        raise Unauthorized("login required")
    sess = s.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(bearer)))
    if sess is None or sess.revoked_at is not None or now >= sess.expires_at:
        raise Unauthorized("session expired or revoked")
    idle = timedelta(minutes=settings.till_idle_minutes if sess.kind == "till" else settings.admin_idle_minutes)
    if now - sess.last_seen_at > idle:
        raise Unauthorized("session locked after inactivity")
    from .db import bind_tenant  # local import: db imports models only

    bind_tenant(s, sess.tenant_id)
    staff = s.get(Staff, sess.staff_id)
    if staff is None or not staff.active or staff.tenant_id != sess.tenant_id:
        raise Unauthorized("staff member is not active")
    device = None
    if sess.device_id is not None:
        device = device_by_token(s, device_token)
        if device is None or device.id != sess.device_id:
            raise Unauthorized("this session belongs to another device")
    if now - sess.last_seen_at >= TOUCH_EVERY:
        s.execute(update(AuthSession).where(AuthSession.id == sess.id).values(last_seen_at=now))
        if device is not None:
            s.execute(update(Device).where(Device.id == device.id).values(last_seen_at=now))
        s.commit()  # the next transaction re-applies the tenant (after_begin hook)
    tenant = s.get(Tenant, sess.tenant_id)
    location = device.location_id if device else None
    roles = roles_at(staff, location) if device else frozenset(r.role for r in staff.roles)
    return Principal(
        tenant_id=tenant.id, tenant_name=tenant.name, staff_id=staff.id, staff_name=staff.display_name,
        roles=roles, session_id=sess.id, device_id=device.id if device else None,
        location_id=location, register_id=device.register_id if device else None,
        kind=sess.kind, mfa=sess.mfa,
        location_ids=frozenset(r.location_id for r in staff.roles if r.role in MANAGER_ROLES),
    )


# ------------------------------------------------------------ enrolment


def create_enrollment_code(s: Session, who: Principal, *, location_id: int, register_id: int | None,
                           device_name: str, now: datetime) -> tuple[EnrollmentCode, str]:
    require_admin(who, location_id)
    loc = s.scalar(select(Location).where(Location.id == location_id, Location.tenant_id == who.tenant_id))
    if loc is None:
        raise NotFound("location not found")
    if register_id is not None:
        reg = s.scalar(select(Register).where(Register.id == register_id, Register.tenant_id == who.tenant_id))
        if reg is None or reg.location_id != location_id:
            raise NotFound("register not found at this location")
    # 16 chars of base32 = 80 bits; single use; 15 minutes.
    raw = base64.b32encode(secrets.token_bytes(10)).decode()
    code = "-".join(raw[i:i + 4] for i in range(0, 16, 4))
    ec = EnrollmentCode(id=str(uuid.uuid4()), tenant_id=who.tenant_id, code_hash=token_hash(_norm_code(code)),
                        location_id=location_id, register_id=register_id, device_name=device_name,
                        created_by=who.staff_id, created_at=now, expires_at=now + ENROLL_TTL)
    s.add(ec)
    audit(s, now, "enroll_code.create", "ok", tenant_id=who.tenant_id, staff_id=who.staff_id,
          session_id=who.session_id, detail=f"location={location_id} register={register_id}")
    s.commit()
    return ec, code


def _norm_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def enroll_device(s: Session, *, code: str, ip: str | None, now: datetime) -> tuple[Device, str]:
    h = token_hash(_norm_code(code))
    ec = s.scalar(select(EnrollmentCode).where(EnrollmentCode.code_hash == h))
    if ec is None or ec.used_at is not None or now >= ec.expires_at:
        if ec is not None:
            from .db import bind_tenant

            bind_tenant(s, ec.tenant_id)
        audit(s, now, "device.enroll", "fail", ip=ip, tenant_id=ec.tenant_id if ec else None)
        s.commit()
        raise InvalidCredentials("enrolment code is invalid, used or expired")
    from .db import bind_tenant

    bind_tenant(s, ec.tenant_id)
    raw = token()
    dev = Device(id=str(uuid.uuid4()), tenant_id=ec.tenant_id, location_id=ec.location_id,
                 register_id=ec.register_id, name=ec.device_name, token_hash=token_hash(raw),
                 enrolled_by=ec.created_by, created_at=now)
    # Single use under concurrency: conditional UPDATE claims the code.
    res = s.execute(update(EnrollmentCode).where(EnrollmentCode.id == ec.id, EnrollmentCode.used_at.is_(None))
                    .values(used_at=now, device_id=dev.id).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        s.rollback()
        raise InvalidCredentials("enrolment code is invalid, used or expired")
    s.add(dev)
    audit(s, now, "device.enroll", "ok", tenant_id=ec.tenant_id, device_id=dev.id, staff_id=ec.created_by, ip=ip)
    s.commit()
    return dev, raw


def revoke_device(s: Session, who: Principal, device_id: str, now: datetime) -> Device:
    dev = s.scalar(select(Device).where(Device.id == device_id, Device.tenant_id == who.tenant_id))
    if dev is None:
        raise NotFound("device not found")
    require_admin(who, dev.location_id)
    if dev.revoked_at is None:
        dev.revoked_at = now
    s.execute(update(AuthSession).where(AuthSession.device_id == dev.id, AuthSession.revoked_at.is_(None))
              .values(revoked_at=now))
    audit(s, now, "device.revoke", "ok", tenant_id=who.tenant_id, staff_id=who.staff_id, device_id=dev.id,
          session_id=who.session_id)
    s.commit()
    return dev


# --------------------------------------------------------------- PIN login


def pin_staff_list(s: Session, device: Device) -> list[Staff]:
    from .db import bind_tenant

    bind_tenant(s, device.tenant_id)
    staff = s.scalars(select(Staff).where(Staff.tenant_id == device.tenant_id, Staff.active.is_(True),
                                          Staff.pin_hash.is_not(None)).order_by(Staff.display_name))
    return [x for x in staff if roles_at(x, device.location_id)]


def _locked(until: datetime | None, now: datetime) -> bool:
    return until is not None and now < until


def _verify_pin_for(s: Session, *, device: Device, staff_id: str, pin: str, now: datetime,
                    ip: str | None, event: str) -> Staff:
    """Shared by PIN login and step-up approval: lockouts + audit."""
    staff = s.scalar(select(Staff).where(Staff.id == staff_id, Staff.tenant_id == device.tenant_id))
    if _locked(device.pin_locked_until, now) or (staff is not None and _locked(staff.pin_locked_until, now)):
        audit(s, now, event, "locked", tenant_id=device.tenant_id, staff_id=staff_id, device_id=device.id, ip=ip)
        s.commit()
        raise Locked("too many wrong PINs; try again later")
    ok = verify_secret(staff.pin_hash if staff is not None and staff.active else None, pin)
    if ok and staff is not None and roles_at(staff, device.location_id):
        staff.failed_pin_attempts, staff.pin_locked_until = 0, None
        device.failed_pin_attempts, device.pin_locked_until = 0, None
        return staff
    # Count failures against the device always and the staff member if known.
    device.failed_pin_attempts += 1
    if device.failed_pin_attempts >= LOCK_AFTER:
        device.pin_locked_until = now + _backoff(device.failed_pin_attempts)
    if staff is not None:
        staff.failed_pin_attempts += 1
        if staff.failed_pin_attempts >= LOCK_AFTER:
            staff.pin_locked_until = now + _backoff(staff.failed_pin_attempts)
    audit(s, now, event, "fail", tenant_id=device.tenant_id, staff_id=staff_id, device_id=device.id, ip=ip)
    s.commit()
    raise InvalidCredentials("wrong PIN")


def pin_login(s: Session, *, device: Device, staff_id: str, pin: str, settings: Settings, now: datetime,
              ip: str | None) -> tuple[AuthSession, str, Staff]:
    from .db import bind_tenant

    bind_tenant(s, device.tenant_id)
    staff = _verify_pin_for(s, device=device, staff_id=staff_id, pin=pin, now=now, ip=ip, event="login.pin")
    sess, raw = _new_session(s, staff=staff, device=device, kind="till", mfa=False, settings=settings, now=now)
    audit(s, now, "login.pin", "ok", tenant_id=device.tenant_id, staff_id=staff.id, device_id=device.id,
          session_id=sess.id, ip=ip)
    s.commit()
    return sess, raw, staff


# ------------------------------------------------------- password + TOTP


def password_login(s: Session, *, tenant_id: str, login: str, password: str, totp: str, box: SecretBox,
                   settings: Settings, now: datetime, ip: str | None) -> tuple[AuthSession, str, Staff]:
    from .db import bind_tenant

    bind_tenant(s, tenant_id)
    staff = s.scalar(select(Staff).where(Staff.tenant_id == tenant_id, Staff.login == login))
    if staff is not None and _locked(staff.login_locked_until, now):
        audit(s, now, "login.password", "locked", tenant_id=tenant_id, staff_id=staff.id, ip=ip)
        s.commit()
        raise Locked("too many failed logins; try again later")
    pw_ok = verify_secret(staff.password_hash if staff is not None else None, password)
    step = None
    if pw_ok and staff.active and staff.totp_secret_enc:
        secret = box.open(staff.totp_secret_enc, aad=staff.id)
        step = totp_match(secret, totp, now, staff.totp_last_step)
    manager = staff is not None and bool({r.role for r in staff.roles} & set(MANAGER_ROLES))
    if not (pw_ok and step is not None and manager and staff.active):
        if staff is not None:
            staff.failed_login_attempts += 1
            if staff.failed_login_attempts >= LOCK_AFTER:
                staff.login_locked_until = now + _backoff(staff.failed_login_attempts)
            audit(s, now, "login.password", "fail", tenant_id=tenant_id, staff_id=staff.id, ip=ip)
            s.commit()
        raise InvalidCredentials("wrong login, password or code")
    # Replay protection: the step is consumed with a conditional UPDATE.
    res = s.execute(update(Staff).where(Staff.id == staff.id, (Staff.totp_last_step.is_(None)) |
                                        (Staff.totp_last_step < step))
                    .values(totp_last_step=step, failed_login_attempts=0, login_locked_until=None)
                    .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        s.rollback()
        raise InvalidCredentials("wrong login, password or code")
    sess, raw = _new_session(s, staff=staff, device=None, kind="admin", mfa=True, settings=settings, now=now)
    audit(s, now, "login.password", "ok", tenant_id=tenant_id, staff_id=staff.id, session_id=sess.id, ip=ip)
    s.commit()
    return sess, raw, staff


def logout(s: Session, who: Principal, now: datetime) -> None:
    s.execute(update(AuthSession).where(AuthSession.id == who.session_id).values(revoked_at=now))
    audit(s, now, "logout", "ok", tenant_id=who.tenant_id, staff_id=who.staff_id, session_id=who.session_id,
          device_id=who.device_id)
    s.commit()


# ------------------------------------------------------------- step-up


def create_approval(s: Session, who: Principal, *, device: Device, action: str, request_hash: str,
                    approver_id: str, pin: str, now: datetime, ip: str | None) -> Approval:
    if who.device_id != device.id:
        raise DeviceRequired("approvals are given on the requesting device")
    approver = _verify_pin_for(s, device=device, staff_id=approver_id, pin=pin, now=now, ip=ip,
                               event=f"approval.{action}")
    roles = roles_at(approver, device.location_id) & set(MANAGER_ROLES)
    if not roles or approver.id == who.staff_id:
        audit(s, now, f"approval.{action}", "denied", tenant_id=who.tenant_id, staff_id=approver.id,
              device_id=device.id, session_id=who.session_id,
              detail="same person" if approver.id == who.staff_id else "not a manager here")
        s.commit()
        raise InvalidApproval("approval needs a different manager or owner at this location")
    a = Approval(id=str(uuid.uuid4()), tenant_id=who.tenant_id, action=action, request_hash=request_hash,
                 approver_id=approver.id, approver_role="owner" if "owner" in roles else "manager",
                 requested_by=who.staff_id, session_id=who.session_id, device_id=device.id,
                 created_at=now, expires_at=now + APPROVAL_TTL)
    s.add(a)
    audit(s, now, f"approval.{action}", "ok", tenant_id=who.tenant_id, staff_id=approver.id,
          device_id=device.id, session_id=who.session_id, detail=f"approval={a.id}")
    s.commit()
    return a


def peek_approval(s: Session, who: Principal, approval_id: str | None, action: str, request_hash: str,
                  now: datetime) -> Approval:
    """Validate (without consuming) an approval for this exact request."""
    a = s.scalar(select(Approval).where(Approval.id == (approval_id or ""), Approval.tenant_id == who.tenant_id))
    if (a is None or a.action != action or a.request_hash != request_hash or a.used_at is not None
            or now >= a.expires_at or a.session_id != who.session_id or a.device_id != who.device_id):
        raise InvalidApproval("a valid manager approval for exactly this request is required")
    return a


def consume_approval(s: Session, approval_id: str, now: datetime) -> None:
    """Single use under concurrency; call inside the business transaction."""
    res = s.execute(update(Approval).where(Approval.id == approval_id, Approval.used_at.is_(None),
                                           Approval.expires_at > now)
                    .values(used_at=now).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise InvalidApproval("approval was already used or expired")


# ------------------------------------------------------------------- admin


def require_admin(who: Principal, location_id: int | None = None) -> None:
    if who.demo:
        return
    if who.kind != "admin" or not who.mfa:
        raise MfaRequired("back-office actions need a password + TOTP login")
    if "owner" in who.roles:
        return
    if "manager" not in who.roles:
        raise Forbidden("manager or owner role required")
    if None in who.location_ids:  # tenant-wide manager
        return
    if location_id is None or location_id not in who.location_ids:
        raise Forbidden("managers may administer only their own locations")


def _check_roles(s: Session, who: Principal, roles: list[tuple[str, int | None]]) -> None:
    for role, loc in roles:
        if role not in ROLES:
            raise WeakSecret(f"unknown role {role}")
        if loc is not None and s.scalar(select(Location.id).where(Location.id == loc,
                                                                 Location.tenant_id == who.tenant_id)) is None:
            raise NotFound("location not found")
        if role == "owner" and "owner" not in who.roles:
            raise Forbidden("only an owner can grant the owner role")
        require_admin(who, loc)


def create_staff(s: Session, who: Principal, *, display_name: str, roles: list[tuple[str, int | None]],
                 pin: str | None, now: datetime) -> Staff:
    _check_roles(s, who, roles)
    if not roles:
        raise WeakSecret("at least one role is required")
    if pin is not None:
        check_pin(pin)
    st = Staff(id=str(uuid.uuid4()), tenant_id=who.tenant_id, display_name=display_name,
               pin_hash=hash_secret(pin) if pin else None, created_at=now)
    st.roles = [StaffRole(tenant_id=who.tenant_id, role=r, location_id=loc) for r, loc in roles]
    s.add(st)
    audit(s, now, "staff.create", "ok", tenant_id=who.tenant_id, staff_id=who.staff_id,
          session_id=who.session_id, detail=f"new={st.id} roles={sorted(r for r, _ in roles)}")
    try:
        s.commit()
    except Exception as e:  # unique name
        s.rollback()
        raise Conflict("a staff member with this name already exists") from e
    return st


def _managed_staff(s: Session, who: Principal, staff_id: str) -> Staff:
    st = s.scalar(select(Staff).where(Staff.id == staff_id, Staff.tenant_id == who.tenant_id))
    if st is None:
        raise NotFound("staff member not found")
    for r in st.roles:
        if r.role == "owner" and "owner" not in who.roles:
            raise Forbidden("only an owner can manage an owner")
        require_admin(who, r.location_id)
    if not st.roles:
        require_admin(who, None)
    return st


def set_pin(s: Session, who: Principal, staff_id: str, pin: str, now: datetime) -> None:
    check_pin(pin)
    st = _managed_staff(s, who, staff_id)
    st.pin_hash, st.failed_pin_attempts, st.pin_locked_until = hash_secret(pin), 0, None
    audit(s, now, "staff.pin_set", "ok", tenant_id=who.tenant_id, staff_id=who.staff_id,
          session_id=who.session_id, detail=f"target={st.id}")
    s.commit()


def set_back_office_credentials(s: Session, who: Principal, staff_id: str, *, login: str, password: str,
                                box: SecretBox, now: datetime) -> str:
    """Owner only. Returns the new TOTP secret ONCE (show as QR, never stored in clear)."""
    if "owner" not in who.roles and not who.demo:
        raise Forbidden("only an owner can set back-office credentials")
    require_admin(who)
    check_password(password)
    st = _managed_staff(s, who, staff_id)
    if not {r.role for r in st.roles} & set(MANAGER_ROLES):
        raise Forbidden("back-office login is only for managers and owners")
    secret = new_totp_secret()
    st.login, st.password_hash = login, hash_secret(password)
    st.totp_secret_enc, st.totp_last_step = box.seal(secret, aad=st.id), None
    st.failed_login_attempts, st.login_locked_until = 0, None
    s.execute(update(AuthSession).where(AuthSession.staff_id == st.id, AuthSession.kind == "admin",
                                        AuthSession.revoked_at.is_(None)).values(revoked_at=now))
    audit(s, now, "staff.credentials_set", "ok", tenant_id=who.tenant_id, staff_id=who.staff_id,
          session_id=who.session_id, detail=f"target={st.id}")
    try:
        s.commit()
    except Exception as e:
        s.rollback()
        raise Conflict("this login is already used") from e
    return secret


def deactivate_staff(s: Session, who: Principal, staff_id: str, now: datetime) -> Staff:
    st = _managed_staff(s, who, staff_id)
    if st.id == who.staff_id:
        raise Forbidden("you cannot deactivate yourself")
    st.active = False
    s.execute(update(AuthSession).where(AuthSession.staff_id == st.id, AuthSession.revoked_at.is_(None))
              .values(revoked_at=now))
    audit(s, now, "staff.deactivate", "ok", tenant_id=who.tenant_id, staff_id=who.staff_id,
          session_id=who.session_id, detail=f"target={st.id}")
    s.commit()
    return st


# --------------------------------------------------------------- bootstrap


def bootstrap_owner(s: Session, *, tenant_id: str, tenant_name: str, owner_name: str, login: str,
                    password: str, pin: str | None, box: SecretBox, now: datetime) -> tuple[Staff, str]:
    """First owner of a tenant (admin CLI, owner DB connection). Refuses if the
    tenant already has an owner, so it cannot be used to take over a tenant."""
    check_password(password)
    if pin is not None:
        check_pin(pin)
    tenant = s.get(Tenant, tenant_id)
    if tenant is None:
        tenant = Tenant(id=tenant_id, name=tenant_name)
        s.add(tenant)
        s.flush()
    has_owner = s.scalar(select(StaffRole.id).where(StaffRole.tenant_id == tenant_id, StaffRole.role == "owner"))
    if has_owner is not None:
        raise Conflict("this tenant already has an owner; use the back office")
    st = Staff(id=str(uuid.uuid4()), tenant_id=tenant_id, display_name=owner_name, login=login,
               password_hash=hash_secret(password), pin_hash=hash_secret(pin) if pin else None, created_at=now)
    secret = new_totp_secret()
    st.totp_secret_enc = box.seal(secret, aad=st.id)
    st.roles = [StaffRole(tenant_id=tenant_id, role="owner", location_id=None)]
    s.add(st)
    audit(s, now, "bootstrap.owner", "ok", tenant_id=tenant_id, staff_id=st.id)
    s.commit()
    return st, secret
