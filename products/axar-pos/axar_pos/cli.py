"""Local admin CLI (owner database connection; run on the server, never exposed).

    python -m axar_pos.cli gen-secret-key
    python -m axar_pos.cli bootstrap --tenant demo --tenant-name "Demo kohvik" \
        --owner-name "Omanik" --login owner
    python -m axar_pos.cli enroll-code --tenant demo --location POOD1 --register KASSA1 --name "Kassa 1"

Passwords are read with getpass (or AXAR_POS_BOOTSTRAP_PASSWORD / _PIN for
automated test setups). Secrets are printed ONCE to this terminal only.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import secrets
import sys

from sqlalchemy import select

from . import auth
from .config import Settings
from .db import init_db, make_engine
from .models import Location, Register, StaffRole


def _factory(settings: Settings):
    return init_db(make_engine(settings.admin_database_url or settings.database_url))


def _secret(prompt: str, env: str, required: bool = True) -> str | None:
    if os.environ.get(env):
        return os.environ[env]
    if not required:
        if not sys.stdin.isatty():  # non-interactive setup: optional secret skipped
            return None
        value = getpass.getpass(f"{prompt} (Enter = none): ")
        return value or None
    first = getpass.getpass(f"{prompt}: ")
    if getpass.getpass(f"{prompt} again: ") != first:
        sys.exit("values do not match")
    return first


def cmd_gen_secret_key(_args, _settings) -> None:
    print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())


def cmd_bootstrap(args, settings: Settings) -> None:
    if not settings.secret_key:
        sys.exit("AXAR_POS_SECRET_KEY must be set (python -m axar_pos.cli gen-secret-key)")
    password = _secret("Owner password (min 12 characters)", "AXAR_POS_BOOTSTRAP_PASSWORD")
    pin = _secret("Owner till PIN (4-8 digits)", "AXAR_POS_BOOTSTRAP_PIN", required=False)
    with _factory(settings)() as s:
        st, secret = auth.bootstrap_owner(
            s, tenant_id=args.tenant, tenant_name=args.tenant_name or args.tenant, owner_name=args.owner_name,
            login=args.login, password=password, pin=pin, box=auth.SecretBox(settings.secret_key),
            now=settings.clock(), pepper=auth.pin_pepper(settings.secret_key))
    print(f"owner staff id: {st.id}")
    print(f"TOTP secret (add to an authenticator app now, shown once): {secret}")
    print(auth.provisioning_uri(secret, f"{args.tenant}:{args.login}"))


def cmd_enroll_code(args, settings: Settings) -> None:
    with _factory(settings)() as s:
        loc = s.scalar(select(Location).where(Location.tenant_id == args.tenant, Location.code == args.location))
        if loc is None:
            sys.exit("location not found")
        reg = None
        if args.register:
            reg = s.scalar(select(Register).where(Register.tenant_id == args.tenant, Register.code == args.register))
            if reg is None:
                sys.exit("register not found")
        owner_id = s.scalar(select(StaffRole.staff_id).where(StaffRole.tenant_id == args.tenant,
                                                             StaffRole.role == "owner"))
        if owner_id is None:
            sys.exit("bootstrap an owner first")
        who = auth.Principal(tenant_id=args.tenant, tenant_name="", staff_id=owner_id, staff_name="cli",
                             roles=frozenset({"owner"}), kind="admin", mfa=True)
        ec, code = auth.create_enrollment_code(s, who, location_id=loc.id, register_id=reg.id if reg else None,
                                               device_name=args.name, now=settings.clock())
    print(f"enrolment code (single use, until {ec.expires_at:%H:%M} UTC): {code}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="axar_pos.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-secret-key")
    b = sub.add_parser("bootstrap")
    b.add_argument("--tenant", required=True)
    b.add_argument("--tenant-name")
    b.add_argument("--owner-name", required=True)
    b.add_argument("--login", required=True)
    e = sub.add_parser("enroll-code")
    e.add_argument("--tenant", required=True)
    e.add_argument("--location", required=True)
    e.add_argument("--register")
    e.add_argument("--name", required=True)
    args = p.parse_args(argv)
    settings = Settings.from_env()
    {"gen-secret-key": cmd_gen_secret_key, "bootstrap": cmd_bootstrap,
     "enroll-code": cmd_enroll_code}[args.cmd](args, settings)


if __name__ == "__main__":
    main()
