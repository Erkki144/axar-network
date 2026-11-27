"""#81 PIN is the identity, the device is the register (owner P0, 27.11.2026).

Real sessions only (no demo identity). Every merge-blocking case from the
issue has a test here: one input (the PIN) opens the device's own register;
the client never names the staff member or the register; two active staff
of one tenant never share a PIN (the database decides, also under
concurrency); tenants are isolated; wrong / unknown / not-granted PINs look
the same and count against the device; register grants are checked at login
AND on every request; the PIN is never stored in clear or written to audit.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select
from test_auth import Client, open_drawer, settings, shop, world  # noqa: F401  (fixtures)

from axar_pos.models import AuthEvent, AuthSession, Sale, Staff

PIN_ONLY = "/api/v1/auth/pin-login"


def pin_only(w, device_token, pin):
    return w.c.post(PIN_ONLY, json={"pin": pin}, headers={"X-Device-Token": device_token})


def session_count(w):
    with w.app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(AuthSession))


# ------------------------------------------------------------ the journey


def test_pin_alone_opens_the_devices_own_register(shop):
    w = shop["w"]
    r = pin_only(w, shop["dev"], "4829")
    assert r.status_code == 200, r.text
    me = Client(w.c, r.json()["token"], shop["dev"]).get("/api/v1/auth/me").json()
    assert me["staff_id"] == shop["cashier"] and me["register_id"] == shop["reg"] and me["kind"] == "till"
    # A different person's PIN on the same device opens the same register as them.
    r = pin_only(w, shop["dev"], "7351")
    assert r.status_code == 200
    me = Client(w.c, r.json()["token"], shop["dev"]).get("/api/v1/auth/me").json()
    assert me["staff_id"] == shop["manager"] and me["register_id"] == shop["reg"]


def test_client_cannot_name_the_register_or_override_identity(shop):
    w = shop["w"]
    other_reg = w.ids[("t-a", "L1", "R2")]
    for extra in ({"register_id": other_reg}, {"tenant_id": "t-b"}, {"role": "owner"}):
        r = w.c.post(PIN_ONLY, json={"pin": "4829", **extra}, headers={"X-Device-Token": shop["dev"]})
        assert r.status_code == 422, (extra, r.text)
    # A staff_id that does not own the PIN is refused (the PIN decides, not the client).
    r = w.c.post(PIN_ONLY, json={"pin": "4829", "staff_id": shop["manager"]}, headers={"X-Device-Token": shop["dev"]})
    assert r.status_code == 401
    # No device token: no login at all.
    assert w.c.post(PIN_ONLY, json={"pin": "4829"}).status_code in (401, 403)


def test_wrong_unknown_and_not_granted_pins_look_identical_and_lock_the_device(shop):
    w, adm = shop["w"], shop["adm"]
    # Someone whose PIN is right but who may not work on this register.
    other = w.staff(adm, "Liis", [("cashier", w.ids[("t-a", "L2")])], "6152")
    before = session_count(w)
    bodies = set()
    for pin in ("1111", "0000", "6152", "abcd", "12"):  # LOCK_AFTER = 5
        r = pin_only(w, shop["dev"], pin)
        assert r.status_code == 401, (pin, r.text)
        bodies.add(r.text)
    assert len(bodies) == 1, bodies  # no oracle: one generic answer
    # 5 failures: the DEVICE is locked (durable, in the database), even for a correct PIN.
    assert pin_only(w, shop["dev"], "4829").status_code == 429
    assert session_count(w) == before
    with w.app.state.session_factory() as s:
        details = [(e.detail or "") + (e.staff_id or "") for e in s.scalars(select(AuthEvent))]
        not_granted = s.scalar(select(func.count()).select_from(AuthEvent).where(
            AuthEvent.detail == "mode=pin_only reason=not_granted", AuthEvent.staff_id == other))
        st = s.get(Staff, shop["cashier"])
    assert not any(p in d for d in details for p in ("4829", "6152", "1111", "7351"))  # never the PIN
    assert not_granted == 1  # the office can see who tried where
    assert st.pin_index.startswith("v1:") and "4829" not in st.pin_index and "4829" not in (st.pin_hash or "")


# ------------------------------------------------------ uniqueness / tenants


def test_two_active_staff_cannot_share_a_pin_but_tenants_can(shop):
    w, adm = shop["w"], shop["adm"]
    loc = shop["loc"]
    r = adm.post("/api/v1/admin/staff", {"display_name": "Teine", "pin": "4829",
                                         "roles": [{"role": "cashier", "location_id": loc}]})
    assert r.status_code == 409 and r.json()["error"] == "pin_unavailable", r.text
    assert "Kati" not in r.text  # never says whose PIN it is
    newbie = w.staff(adm, "Teine", [("cashier", loc)], "5937")
    r = adm.put(f"/api/v1/admin/staff/{newbie}/pin", {"pin": "4829"})
    assert r.status_code == 409 and r.json()["error"] == "pin_unavailable"
    # Tenant B may use the same digits; each device finds only its own tenant's person.
    adm_b = w.admin("t-b")
    loc_b = w.ids[("t-b", "L1")]
    b_staff = w.staff(adm_b, "Bee", [("cashier", loc_b)], "4829")
    dev_b = w.device(adm_b, tenant="t-b", loc="L1", reg="R1")
    r = pin_only(w, dev_b, "4829")
    assert r.status_code == 200
    me = Client(w.c, r.json()["token"], dev_b).get("/api/v1/auth/me").json()
    assert me["staff_id"] == b_staff and me["tenant_id"] == "t-b"
    # A tenant-B-only PIN gives nothing on a tenant-A device.
    w.staff(adm_b, "Bee2", [("cashier", loc_b)], "3816")
    assert pin_only(w, shop["dev"], "3816").status_code == 401


def test_concurrent_assignment_of_one_pin_has_exactly_one_winner(shop):
    w, adm = shop["w"], shop["adm"]
    loc = shop["loc"]
    a = w.staff(adm, "Ann", [("cashier", loc)], "2846")
    b = w.staff(adm, "Ben", [("cashier", loc)], "3957")

    def put(staff_id):
        return adm.put(f"/api/v1/admin/staff/{staff_id}/pin", {"pin": "6284"}).status_code

    with ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(put, [a, b]))
    assert codes == [204, 409], codes
    with w.app.state.session_factory() as s:
        from axar_pos.db import bind_tenant

        bind_tenant(s, "t-a")
        holders = s.scalars(select(Staff.pin_index).where(Staff.tenant_id == "t-a",
                                                         Staff.pin_index.is_not(None))).all()
    assert len(holders) == len(set(holders))
    winner = Client(w.c, pin_only(w, shop["dev"], "6284").json()["token"], shop["dev"]).get("/api/v1/auth/me")
    assert winner.json()["staff_id"] in (a, b)


def test_server_generated_pin_is_unique_and_shown_once(shop):
    w, adm = shop["w"], shop["adm"]
    r = adm.post("/api/v1/admin/staff", {"display_name": "Uus", "roles": [{"role": "cashier",
                                                                            "location_id": shop["loc"]}]})
    st = r.json()["id"]
    r = adm.post(f"/api/v1/admin/staff/{st}/pin/generate")
    assert r.status_code == 200, r.text
    pin = r.json()["pin"]
    assert pin.isdigit() and len(pin) == 6
    r = pin_only(w, shop["dev"], pin)
    assert r.status_code == 200
    staff = {x["id"]: x for x in adm.get("/api/v1/admin/staff").json()}
    assert staff[st]["pin_indexed"] is True and "pin" not in staff[st]


# ------------------------------------------------------------ grants


def test_register_grant_is_checked_at_login_and_on_every_request(shop):
    w, adm, till = shop["w"], shop["adm"], shop["till"]
    r1, r2 = shop["reg"], w.ids[("t-a", "L1", "R2")]
    assert till.get("/api/v1/auth/me").status_code == 200
    r = adm.put(f"/api/v1/admin/staff/{shop['cashier']}/registers", {"register_ids": [r2]})
    assert r.status_code == 200 and r.json()["register_ids"] == [r2], r.text
    # The open session on R1 ends at the very next request.
    assert till.get("/api/v1/auth/me").status_code == 401
    r = till.post("/api/v1/sales/cash", {"register_id": r1, "tendered_cents": 1000, "lines": []},
                  {"Idempotency-Key": "k-after-revoke"})
    assert r.status_code == 401
    with w.app.state.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Sale)) == 0
        assert s.scalar(select(func.count()).select_from(AuthEvent).where(
            AuthEvent.event == "session.revoked", AuthEvent.outcome == "grant")) == 1
    # R1 refuses the PIN; the R2 device accepts it and opens R2.
    assert pin_only(w, shop["dev"], "4829").status_code == 401
    dev2 = w.device(adm, reg="R2", name="Kassa 2")
    r = pin_only(w, dev2, "4829")
    assert r.status_code == 200
    assert Client(w.c, r.json()["token"], dev2).get("/api/v1/auth/me").json()["register_id"] == r2
    # Empty list = every register at the role location again.
    assert adm.put(f"/api/v1/admin/staff/{shop['cashier']}/registers", {"register_ids": []}).status_code == 200
    assert pin_only(w, shop["dev"], "4829").status_code == 200


def test_grant_needs_a_role_there_and_stays_in_the_tenant(shop):
    w, adm = shop["w"], shop["adm"]
    url = f"/api/v1/admin/staff/{shop['cashier']}/registers"
    r = adm.put(url, {"register_ids": [w.ids[("t-a", "L2", "R1")]]})
    assert r.status_code == 422 and r.json()["error"] == "invalid_grant"
    assert adm.put(url, {"register_ids": [w.ids[("t-b", "L1", "R1")]]}).status_code == 404
    assert adm.put(url, {"register_ids": [999999]}).status_code == 404
    assert adm.put(url, {"register_ids": ["1"]}).status_code == 422
    # A till token never reaches grant management (#76 realms).
    r = shop["till"].put(url, {"register_ids": []})
    assert r.status_code == 403 and r.json()["error"] == "wrong_realm"


def test_role_removal_or_deactivation_ends_the_open_session_and_frees_the_pin(shop):
    w, adm, till = shop["w"], shop["adm"], shop["till"]
    r = adm.post(f"/api/v1/admin/staff/{shop['cashier']}/deactivate")
    assert r.status_code in (200, 204), r.text
    assert till.get("/api/v1/auth/me").status_code == 401
    assert pin_only(w, shop["dev"], "4829").status_code == 401
    # The PIN of a deactivated person can be given to a new colleague.
    w.staff(adm, "Järglane", [("cashier", shop["loc"])], "4829")
    assert pin_only(w, shop["dev"], "4829").status_code == 200


# ----------------------------------------------------- legacy migration


def test_legacy_pin_gets_its_index_on_the_first_name_grid_login(shop):
    w = shop["w"]
    with w.app.state.session_factory() as s:
        from axar_pos.db import bind_tenant

        bind_tenant(s, "t-a")
        st = s.get(Staff, shop["manager"])
        st.pin_index = None  # as before migration 0011
        s.commit()
    assert pin_only(w, shop["dev"], "7351").status_code == 401  # not findable yet
    staff = {x["id"]: x for x in shop["adm"].get("/api/v1/admin/staff").json()}
    assert staff[shop["manager"]]["pin_indexed"] is False  # the office sees who still needs it
    r = w.c.post(PIN_ONLY, json={"staff_id": shop["manager"], "pin": "7351"}, headers={"X-Device-Token": shop["dev"]})
    assert r.status_code == 200
    assert pin_only(w, shop["dev"], "7351").status_code == 200
    with w.app.state.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(AuthEvent).where(
            AuthEvent.event == "pin_index.backfill", AuthEvent.outcome == "ok")) == 1


def test_step_up_approval_and_money_still_work_after_pin_only_login(shop):
    """Manager step-up for refunds / cash-out is unchanged (owner: keep it)."""
    w = shop["w"]
    till = Client(w.c, pin_only(w, shop["dev"], "4829").json()["token"], shop["dev"])
    open_drawer(till, shop["reg"])
    sale = till.sell(shop["reg"])
    assert sale.status_code == 201, sale.text
    r = till.post("/api/v1/sales/{}/refunds".format(sale.json()["id"]),
                  {"register_id": shop["reg"], "lines": [{"line_no": 1, "quantity": 1}], "reason": "vale toode"},
                  {"Idempotency-Key": "refund-without-manager"})
    assert r.status_code in (403, 428), r.text  # needs the manager's own PIN as a step-up
