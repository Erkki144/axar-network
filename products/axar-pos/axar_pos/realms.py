"""Session realms (#76): the till (POS) and the office are separate worlds.

Owner requirement 27.11.2026: the till window and the office window are
separate and each has its own login. From the till there is no way into office
actions or views, and the other way round.

Every API route is assigned exactly one realm. ``check`` runs inside the
identity dependency, i.e. on EVERY authenticated request, before the handler:

    pos     till sessions (PIN on an enrolled device, kind="till")
    office  back-office sessions (password + TOTP, kind="admin")
    read    both may read (receipts, sales history, daily report, live feed);
            scope (location / register) is still checked by the handler
    auth    sign-in plumbing; any session or none

The same person may hold both a till role and an office role, but needs two
logins: a till token never changes prices, an office token never sells or
moves cash. A manager approving a till action does it with a one-time step-up
proof on that till (``/auth/approvals``), never with an office token.

Demo identity (local development only, refused by the pilot profile) is not a
session and is allowed in both realms.
"""

from __future__ import annotations

POS, OFFICE, READ, AUTH = "pos", "office", "read", "auth"

# (path template) -> realm. Methods share a realm per path.
ROUTE_REALMS: dict[str, str] = {
    # --- till: selling, tables, kitchen, drawer, payments, sync from edge tills
    "/api/v1/products": POS,
    "/api/v1/registers": POS,
    "/api/v1/registers/{register_id}/drawer": POS,
    "/api/v1/drawer/open": POS,
    "/api/v1/drawer/{drawer_id}": POS,
    "/api/v1/drawer/{drawer_id}/close": POS,
    "/api/v1/drawer/{drawer_id}/movements": POS,
    "/api/v1/sales/quote": POS,
    "/api/v1/sales/cash": POS,
    "/api/v1/tables": POS,
    "/api/v1/tables/{table_id}/order": POS,
    "/api/v1/orders/{order_id}": POS,
    "/api/v1/orders/{order_id}/lines": POS,
    "/api/v1/orders/{order_id}/kitchen": POS,
    "/api/v1/orders/{order_id}/pay-cash": POS,
    "/api/v1/orders/{order_id}/bill": POS,
    "/api/v1/orders/{order_id}/payments": POS,
    "/api/v1/orders/{order_id}/settle": POS,
    "/api/v1/payments/{payment_id}/reconcile": POS,
    "/api/v1/kitchen/tickets": POS,
    "/api/v1/kitchen/tickets/{ticket_id}/ready": POS,
    "/api/v1/operations/{idempotency_key}": POS,
    "/api/v1/sync/push": POS,
    "/api/v1/sync/status": POS,
    "/api/v1/auth/approvals": POS,               # manager PIN step-up ON the till
    # --- both may read (handlers still apply location / register scope)
    "/api/v1/sales": READ,
    "/api/v1/sales/{sale_id}": READ,
    "/api/v1/sales/{sale_id}/refunds": READ,     # POST (create refund) is checked below as POS
    "/api/v1/refunds/{refund_id}": READ,
    "/api/v1/refunds/{refund_id}/receipt.txt": READ,
    "/api/v1/sales/{sale_id}/receipt.txt": READ,
    "/api/v1/reports/daily": READ,
    "/api/v1/events/stream": READ,
    # --- sign-in plumbing
    "/api/v1/auth/config": AUTH,
    "/api/v1/auth/devices/enroll": AUTH,
    "/api/v1/auth/device": AUTH,
    "/api/v1/auth/pin-login": AUTH,
    "/api/v1/auth/login": AUTH,
    "/api/v1/auth/logout": AUTH,
    "/api/v1/auth/me": AUTH,
}
# Whole office areas by prefix: master data, prices, stock, people, devices,
# receivables, reconciliation and the offline review queue.
OFFICE_PREFIXES = ("/api/v1/admin/", "/api/v1/catalog/", "/api/v1/pricing/", "/api/v1/inventory/",
                   "/api/v1/receivables/", "/api/v1/sync/quarantine")

# Writes on a READ path that belong to one realm.
WRITE_OVERRIDES: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/sales/{sale_id}/refunds"): POS,
}

ALLOWED_KINDS = {POS: {"till"}, OFFICE: {"admin"}, READ: {"till", "admin"}}


def realm_of(method: str, path: str) -> str | None:
    if (method, path) in WRITE_OVERRIDES:
        return WRITE_OVERRIDES[(method, path)]
    if path in ROUTE_REALMS:
        return ROUTE_REALMS[path]
    if path.startswith(OFFICE_PREFIXES):
        return OFFICE
    return None


class WrongRealm(Exception):
    def __init__(self, realm: str | None, kind: str):
        self.realm, self.kind = realm, kind
        where = {"pos": "the till (PIN on an enrolled device)", "office": "the office (password + TOTP)"}
        super().__init__(f"this action belongs to {where.get(realm, realm or 'no realm')}; "
                         f"sign in there separately" if realm else "route has no realm (fail closed)")


def check(method: str, path: str, kind: str, demo: bool) -> None:
    """Raise WrongRealm unless a session of ``kind`` may use this route."""
    realm = realm_of(method, path)
    if realm is None:
        raise WrongRealm(None, kind)       # a route without a realm is never reachable
    if realm == AUTH or demo:
        return
    if kind not in ALLOWED_KINDS[realm]:
        raise WrongRealm(realm, kind)
