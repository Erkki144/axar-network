"""Server-side identity (issue #2, RFC-0002 §3 threat model): every row of the
threat table has a negative test here."""

import base64
import json
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from axar_pos import auth
from axar_pos.api import create_app
from axar_pos.config import Settings
from axar_pos.models import (AuthEvent, AuthSession, DiningTable, Location, Product, Refund, Register, Sale,
                             Staff, Tenant)

TEST_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()  # test-only server key
OWNER_PW = "owner-password-123"
MGR_PW = "manager-password-123"


@pytest.fixture
def settings(clock, database_urls) -> Settings:
    app_url, admin_url = database_urls
    return Settings(database_url=app_url, admin_database_url=admin_url, demo_identity=False,
                    secret_key=TEST_KEY, clock=clock)


@pytest.fixture
def world(settings, clock):
    """Tenants A (two locations) and B, each with an owner (password + TOTP)."""
    app = create_app(settings)
    box = auth.SecretBox(TEST_KEY)
    ids = {}
    with app.state.session_factory() as s:
        for tid in ("t-a", "t-b"):
            s.add(Tenant(id=tid, name=f"Firma {tid}"))
        s.flush()
        for tid, code in (("t-a", "L1"), ("t-a", "L2"), ("t-b", "L1")):
            loc = Location(tenant_id=tid, code=code, name=f"{tid} {code}")
            s.add(loc)
            s.flush()
            ids[(tid, code)] = loc.id
            for r in ("R1", "R2"):
                reg = Register(tenant_id=tid, location_id=loc.id, code=f"{code}{r}", name=f"Kassa {code}{r}")
                s.add(reg)
                s.flush()
                ids[(tid, code, r)] = reg.id
            t = DiningTable(tenant_id=tid, location_id=loc.id, code=f"T{code}", name=f"Laud {code}")
            s.add(t)
        s.add_all([Product(tenant_id="t-a", sku="KOHV", name="Kohv", price_cents=250),
                   Product(tenant_id="t-b", sku="TEE", name="Tee", price_cents=200)])
        s.commit()
        secrets_ = {}
        for tid in ("t-a", "t-b"):
            st, secret = auth.bootstrap_owner(s, tenant_id=tid, tenant_name="", owner_name="Omanik",
                                              login="owner", password=OWNER_PW, pin=None, box=box, now=clock())
            secrets_[tid] = secret
    client = TestClient(app)
    client.__enter__()
    w = World(app, client, clock, ids, secrets_)
    yield w
    client.__exit__(None, None, None)
    app.state.engine.dispose()
    app.state.admin_engine.dispose()


class World:
    def __init__(self, app, client, clock, ids, owner_secrets):
        self.app, self.c, self.clock, self.ids, self.owner_secrets = app, client, clock, ids, owner_secrets
        self.step_offset = 0

    def totp(self, secret):
        # Each call uses the next 30-s step so replay protection never trips by accident.
        self.clock.at += timedelta(seconds=31)
        return auth.totp_at(secret, int(self.clock.at.timestamp()) // 30)

    def admin(self, tenant="t-a", login="owner", password=OWNER_PW, secret=None):
        r = self.c.post("/api/v1/auth/login", json={"tenant_id": tenant, "login": login, "password": password,
                                                     "totp": self.totp(secret or self.owner_secrets[tenant])})
        assert r.status_code == 200, r.text
        return Client(self.c, r.json()["token"])

    def staff(self, adm, name, roles, pin):
        r = adm.post("/api/v1/admin/staff", {"display_name": name, "pin": pin,
                                             "roles": [{"role": ro, "location_id": loc} for ro, loc in roles]})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def device(self, adm, tenant="t-a", loc="L1", reg="R1", name="Kassa"):
        body = {"location_id": self.ids[(tenant, loc)], "device_name": name,
                "register_id": self.ids[(tenant, loc, reg)] if reg else None}
        code = adm.post("/api/v1/admin/enrollment-codes", body)
        assert code.status_code == 201, code.text
        r = self.c.post("/api/v1/auth/devices/enroll", json={"code": code.json()["code"]})
        assert r.status_code == 201, r.text
        return r.json()["device_token"]

    def till(self, device_token, staff_id, pin):
        r = self.c.post("/api/v1/auth/pin-login", json={"staff_id": staff_id, "pin": pin},
                        headers={"X-Device-Token": device_token})
        assert r.status_code == 200, r.text
        return Client(self.c, r.json()["token"], device_token)


class Client:
    def __init__(self, c, token, device=None):
        self.c, self.token, self.device = c, token, device

    @property
    def h(self):
        h = {"Authorization": f"Bearer {self.token}"}
        if self.device:
            h["X-Device-Token"] = self.device
        return h

    def get(self, path):
        return self.c.get(path, headers=self.h)

    def post(self, path, body=None, headers=None):
        return self.c.post(path, json=body, headers={**self.h, **(headers or {})})

    def put(self, path, body=None):
        return self.c.put(path, json=body, headers=self.h)

    def sell(self, register_id, qty=1, tendered=1000, key=None):
        pid = next(p["id"] for p in self.get("/api/v1/products").json())
        return self.post("/api/v1/sales/cash", {"register_id": register_id, "tendered_cents": tendered,
                                                "lines": [{"product_id": pid, "quantity": qty}]},
                         {"Idempotency-Key": key or str(uuid.uuid4())})


@pytest.fixture
def shop(world):
    """Tenant A, location L1: owner (back office), cashier, manager, kitchen; one till on R1."""
    adm = world.admin()
    loc = world.ids[("t-a", "L1")]
    cashier = world.staff(adm, "Kati", [("cashier", loc), ("waiter", loc)], "4829")
    manager = world.staff(adm, "Mart", [("manager", loc)], "7351")
    cook = world.staff(adm, "Kokk", [("kitchen", loc)], "9164")
    dev = world.device(adm)
    till = world.till(dev, cashier, "4829")
    return {"w": world, "adm": adm, "cashier": cashier, "manager": manager, "cook": cook, "dev": dev,
            "till": till, "reg": world.ids[("t-a", "L1", "R1")], "loc": loc}


def open_drawer(cl, reg, float_cents=5000):
    r = cl.post("/api/v1/drawer/open", {"register_id": reg, "opening_float_cents": float_cents})
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------ startup / headers


def test_demo_identity_and_secret_key_cannot_coexist(tmp_path):
    with pytest.raises(RuntimeError, match="cannot be enabled"):
        create_app(Settings(database_url=f"sqlite:///{tmp_path / 'x.db'}", demo_identity=True, secret_key=TEST_KEY))


def test_forged_identity_headers_grant_nothing(shop):
    c = shop["w"].c
    forged = {"X-Tenant-ID": "t-a", "X-Cashier-ID": "x", "X-Role": "owner"}
    assert c.get("/api/v1/products", headers=forged).status_code == 401
    # A real cashier session plus X-Role: owner is still only a cashier.
    r = shop["till"].c.get("/api/v1/reports/daily", headers={**shop["till"].h, "X-Role": "owner"})
    assert r.status_code == 403


# ---------------------------------------------------- back-office login


def test_password_login_needs_correct_totp_and_rejects_replay(world):
    secret = world.owner_secrets["t-a"]
    code = world.totp(secret)
    body = {"tenant_id": "t-a", "login": "owner", "password": OWNER_PW, "totp": code}
    assert world.c.post("/api/v1/auth/login", json={**body, "totp": "000000" if code != "000000" else "111111"}
                        ).status_code == 401
    assert world.c.post("/api/v1/auth/login", json=body).status_code == 200
    assert world.c.post("/api/v1/auth/login", json=body).status_code == 401      # same code replayed
    assert world.c.post("/api/v1/auth/login", json={**body, "password": "wrong-password-123"}).status_code == 401


def test_password_login_lockout_even_with_correct_credentials(world):
    for _ in range(auth.LOCK_AFTER):
        r = world.c.post("/api/v1/auth/login", json={"tenant_id": "t-a", "login": "owner",
                                                      "password": "nope-nope-nope", "totp": "123456"})
        assert r.status_code == 401
    good = {"tenant_id": "t-a", "login": "owner", "password": OWNER_PW,
            "totp": auth.totp_at(world.owner_secrets["t-a"], int(world.clock.at.timestamp()) // 30)}
    assert world.c.post("/api/v1/auth/login", json=good).status_code == 429
    world.clock.at += timedelta(minutes=5)
    assert world.admin().get("/api/v1/auth/me").json()["mfa"] is True


def test_unknown_login_and_other_tenant_owner_get_same_answer(world):
    for body in ({"tenant_id": "t-a", "login": "ghost"}, {"tenant_id": "t-b", "login": "owner"}):
        r = world.c.post("/api/v1/auth/login", json={**body, "password": OWNER_PW,
                                                      "totp": world.totp(world.owner_secrets["t-a"])})
        assert r.status_code == 401 and r.json()["error"] == "invalid_credentials"


def test_totp_secret_is_encrypted_at_rest(world):
    with world.app.state.session_factory() as s:
        st = s.scalar(select(Staff).where(Staff.tenant_id == "t-a", Staff.login == "owner"))
        assert st.totp_secret_enc.startswith("v1:") and world.owner_secrets["t-a"] not in st.totp_secret_enc
        assert st.password_hash.startswith("$argon2id$")


# ------------------------------------------------------------ devices


def test_enrollment_code_is_single_use_and_expires(world):
    adm = world.admin()
    body = {"location_id": world.ids[("t-a", "L1")], "register_id": world.ids[("t-a", "L1", "R1")],
            "device_name": "Kassa"}
    code = adm.post("/api/v1/admin/enrollment-codes", body).json()["code"]
    assert world.c.post("/api/v1/auth/devices/enroll", json={"code": code}).status_code == 201
    assert world.c.post("/api/v1/auth/devices/enroll", json={"code": code}).status_code == 401
    code2 = adm.post("/api/v1/admin/enrollment-codes", body).json()["code"]
    world.clock.at += auth.ENROLL_TTL + timedelta(seconds=1)
    assert world.c.post("/api/v1/auth/devices/enroll", json={"code": code2}).status_code == 401


def test_enrollment_needs_mfa_back_office_session(shop):
    body = {"location_id": shop["loc"], "register_id": shop["reg"], "device_name": "X"}
    r = shop["till"].post("/api/v1/admin/enrollment-codes", body)
    assert r.status_code == 403 and r.json()["error"] == "mfa_required"


def test_device_token_is_stored_hashed(shop):
    with shop["w"].app.state.session_factory() as s:
        hashes = [d.token_hash for d in s.scalars(select(auth.Device))]
    assert shop["dev"] not in hashes and auth.token_hash(shop["dev"]) in hashes


# ------------------------------------------------------------- PIN login


def test_correct_pin_from_unenrolled_or_foreign_device_is_rejected(shop, world):
    c = world.c
    body = {"staff_id": shop["cashier"], "pin": "4829"}
    assert c.post("/api/v1/auth/pin-login", json=body).status_code == 401
    assert c.post("/api/v1/auth/pin-login", json=body, headers={"X-Device-Token": "forged"}).status_code == 401
    adm_b = world.admin("t-b")
    dev_b = world.device(adm_b, tenant="t-b")
    assert c.post("/api/v1/auth/pin-login", json=body, headers={"X-Device-Token": dev_b}).status_code == 401


def test_pin_works_only_at_staff_location(shop, world):
    dev_l2 = world.device(shop["adm"], loc="L2")
    r = world.c.post("/api/v1/auth/pin-login", json={"staff_id": shop["cashier"], "pin": "4829"},
                     headers={"X-Device-Token": dev_l2})
    assert r.status_code == 401
    staff = world.c.get("/api/v1/auth/device", headers={"X-Device-Token": dev_l2}).json()["staff"]
    assert shop["cashier"] not in [x["id"] for x in staff]


def test_pin_brute_force_locks_staff_and_device(shop, world):
    c, dev = world.c, shop["dev"]
    for _ in range(auth.LOCK_AFTER):
        r = c.post("/api/v1/auth/pin-login", json={"staff_id": shop["cashier"], "pin": "0000"},
                   headers={"X-Device-Token": dev})
        assert r.status_code == 401
    r = c.post("/api/v1/auth/pin-login", json={"staff_id": shop["cashier"], "pin": "4829"},
               headers={"X-Device-Token": dev})
    assert r.status_code == 429                                   # locked even with the right PIN
    r = c.post("/api/v1/auth/pin-login", json={"staff_id": shop["manager"], "pin": "7351"},
               headers={"X-Device-Token": dev})
    assert r.status_code == 429                                   # the device is locked too
    world.clock.at += timedelta(seconds=31)
    assert world.till(dev, shop["cashier"], "4829").get("/api/v1/auth/me").status_code == 200


def test_weak_pins_are_refused(shop):
    for pin in ("1111", "1234", "98765", "12a4", "123"):
        r = shop["adm"].put(f"/api/v1/admin/staff/{shop['cashier']}/pin", {"pin": pin})
        assert r.status_code == 422, pin


# ----------------------------------------------------------- sessions


def test_till_session_is_bound_to_its_device(shop, world):
    till = shop["till"]
    assert till.c.get("/api/v1/products", headers={"Authorization": f"Bearer {till.token}"}).status_code == 401
    other = world.device(shop["adm"], reg="R2")
    r = till.c.get("/api/v1/products", headers={"Authorization": f"Bearer {till.token}", "X-Device-Token": other})
    assert r.status_code == 401


def test_idle_and_absolute_expiry(shop, world, settings):
    till = shop["till"]
    world.clock.at += timedelta(minutes=settings.till_idle_minutes - 1)
    assert till.get("/api/v1/products").status_code == 200      # activity extends idle window
    world.clock.at += timedelta(minutes=settings.till_idle_minutes + 1)
    assert till.get("/api/v1/products").status_code == 401
    fresh = world.till(shop["dev"], shop["cashier"], "4829")
    for _ in range(settings.session_absolute_hours * 6):
        world.clock.at += timedelta(minutes=10)
        if fresh.get("/api/v1/products").status_code != 200:
            break
    assert fresh.get("/api/v1/products").status_code == 401


def test_logout_and_deactivation_and_device_revoke_take_effect_at_once(shop, world):
    till = shop["till"]
    assert till.post("/api/v1/auth/logout").status_code == 204
    assert till.get("/api/v1/products").status_code == 401
    t2 = world.till(shop["dev"], shop["cashier"], "4829")
    assert shop["adm"].post(f"/api/v1/admin/staff/{shop['cashier']}/deactivate").status_code == 200
    assert t2.get("/api/v1/products").status_code == 401
    mgr = world.till(shop["dev"], shop["manager"], "7351")
    dev_id = mgr.get("/api/v1/auth/me").json()["device_id"]
    assert shop["adm"].post(f"/api/v1/admin/devices/{dev_id}/revoke").status_code == 200
    assert mgr.get("/api/v1/products").status_code == 401
    assert world.c.get("/api/v1/auth/device", headers={"X-Device-Token": shop["dev"]}).status_code == 401


def test_session_tokens_are_stored_hashed(shop):
    with shop["w"].app.state.session_factory() as s:
        hashes = set(s.scalars(select(AuthSession.token_hash)))
    assert shop["till"].token not in hashes and auth.token_hash(shop["till"].token) in hashes


# ------------------------------------------------------ roles and scope


def test_role_matrix(shop, world):
    kitchen = world.till(shop["dev"], shop["cook"], "9164")
    assert kitchen.sell(shop["reg"]).status_code == 403
    assert kitchen.get("/api/v1/kitchen/tickets").status_code == 200
    assert shop["till"].get("/api/v1/reports/daily").status_code == 403
    assert shop["till"].get("/api/v1/admin/staff").status_code == 403
    assert shop["till"].get("/api/v1/receivables/invoices").status_code == 403     # C3: managers only
    mgr_till = world.till(shop["dev"], shop["manager"], "7351")
    r = mgr_till.get("/api/v1/admin/staff")
    assert r.status_code == 403 and r.json()["error"] == "mfa_required"   # PIN is not strong auth
    assert shop["adm"].get("/api/v1/reports/daily").status_code == 200
    assert shop["adm"].get("/api/v1/receivables/invoices").status_code == 200
    me = shop["till"].get("/api/v1/auth/me").json()
    assert set(me["roles"]) == {"cashier", "waiter"} and "refund.request" in me["permissions"]


def test_money_actions_only_on_the_device_register(shop):
    till, reg = shop["till"], shop["reg"]
    other_reg = shop["w"].ids[("t-a", "L1", "R2")]
    assert till.post("/api/v1/drawer/open", {"register_id": other_reg, "opening_float_cents": 0}).status_code == 403
    open_drawer(till, reg)
    assert till.sell(other_reg).status_code == 403
    assert till.sell(reg).status_code == 201
    assert [r["id"] for r in till.get("/api/v1/registers").json()] == [reg]
    # A back-office session has no device: no selling from the office browser.
    r = shop["adm"].sell(reg)
    assert r.status_code == 403 and r.json()["error"] == "device_required"


def test_records_carry_staff_device_and_session(shop):
    open_drawer(shop["till"], shop["reg"])
    sale = shop["till"].sell(shop["reg"]).json()
    me = shop["till"].get("/api/v1/auth/me").json()
    with shop["w"].app.state.session_factory() as s:
        row = s.get(Sale, sale["id"])
        sess = s.scalar(select(AuthSession).where(AuthSession.token_hash == auth.token_hash(shop["till"].token)))
    assert (row.cashier_id, row.device_id, row.session_id) == (shop["cashier"], me["device_id"], sess.id)


def test_tenant_comes_from_the_session_never_the_request(shop, world):
    adm_b = world.admin("t-b")
    cashier_b = world.staff(adm_b, "Bea", [("cashier", None)], "5274")
    till_b = world.till(world.device(adm_b, tenant="t-b"), cashier_b, "5274")
    open_drawer(till_b, world.ids[("t-b", "L1", "R1")])
    sale_b = till_b.sell(world.ids[("t-b", "L1", "R1")]).json()
    for path in (f"/api/v1/sales/{sale_b['id']}", f"/api/v1/sales/{sale_b['id']}/receipt.txt"):
        assert shop["till"].get(path).status_code == 404
    assert {p["sku"] for p in shop["till"].get("/api/v1/products").json()} == {"KOHV"}


def test_location_scope_for_tables(shop, world):
    tables = shop["till"].get("/api/v1/tables").json()
    assert [t["code"] for t in tables] == ["TL1"]
    with world.app.state.session_factory() as s:
        other = s.scalar(select(DiningTable.id).where(DiningTable.code == "TL2"))
    assert shop["till"].post(f"/api/v1/tables/{other}/order").status_code == 404


def test_manager_cannot_grant_owner_or_manage_other_location(shop, world):
    mgr_secret = shop["adm"].put(f"/api/v1/admin/staff/{shop['manager']}/credentials",
                                 {"login": "mart", "password": MGR_PW}).json()["totp_secret"]
    mgr = world.admin(login="mart", password=MGR_PW, secret=mgr_secret)
    loc2 = world.ids[("t-a", "L2")]
    r = mgr.post("/api/v1/admin/staff", {"display_name": "Uus", "pin": "5829",
                                         "roles": [{"role": "owner", "location_id": None}]})
    assert r.status_code == 403
    r = mgr.post("/api/v1/admin/staff", {"display_name": "Uus", "pin": "5829",
                                         "roles": [{"role": "cashier", "location_id": loc2}]})
    assert r.status_code == 403
    r = mgr.post("/api/v1/admin/staff", {"display_name": "Uus", "pin": "5829",
                                         "roles": [{"role": "cashier", "location_id": shop["loc"]}]})
    assert r.status_code == 201
    assert mgr.put(f"/api/v1/admin/staff/{shop['cashier']}/credentials",
                   {"login": "kati", "password": MGR_PW}).status_code == 403   # owner only


# --------------------------------------------------------------- step-up


def refund_body(shop, sale, qty=1, reason="klient tagastas"):
    return {"register_id": shop["reg"], "lines": [{"line_no": 1, "quantity": qty}], "reason": reason}


def approve(shop, sale_id, body, approver=None, pin="7351", client=None):
    cl = client or shop["till"]
    return cl.post("/api/v1/auth/approvals", {
        "action": "refund", "approver_id": approver or shop["manager"], "pin": pin,
        "request": {"sale_id": sale_id, **body}})


def refund(shop, sale_id, body, approval=None, key=None, client=None):
    cl = client or shop["till"]
    h = {"Idempotency-Key": key or str(uuid.uuid4())}
    if approval:
        h["X-Approval-Id"] = approval
    return cl.post(f"/api/v1/sales/{sale_id}/refunds", body, h)


@pytest.fixture
def sold(shop):
    open_drawer(shop["till"], shop["reg"])
    return shop["till"].sell(shop["reg"], qty=3).json()


def test_refund_needs_a_different_managers_approval(shop, sold):
    body = refund_body(shop, sold)
    r = refund(shop, sold["id"], body)
    assert r.status_code == 403 and r.json()["error"] == "approval_invalid"
    assert approve(shop, sold["id"], body, approver=shop["cashier"], pin="4829").status_code == 403  # not manager
    a = approve(shop, sold["id"], body)
    assert a.status_code == 201
    r = refund(shop, sold["id"], body, a.json()["approval_id"], key="refund-key-0001")
    assert r.status_code == 201, r.text
    assert (r.json()["approved_by"], r.json()["approver_role"]) == (shop["manager"], "manager")
    assert r.json()["cashier_id"] == shop["cashier"]
    # Idempotent replay works although the approval is used up.
    again = refund(shop, sold["id"], body, a.json()["approval_id"], key="refund-key-0001")
    assert again.status_code == 200 and again.json()["id"] == r.json()["id"]
    # ... but the approval cannot authorise a second refund.
    assert refund(shop, sold["id"], body, a.json()["approval_id"]).status_code == 403


def test_manager_cannot_approve_own_refund(shop, sold, world):
    mgr = world.till(shop["dev"], shop["manager"], "7351")
    body = refund_body(shop, sold)
    r = approve(shop, sold["id"], body, client=mgr)
    assert r.status_code == 403 and r.json()["error"] == "approval_invalid"


def test_approval_is_bound_to_the_exact_request_and_expires(shop, sold, world):
    body = refund_body(shop, sold)
    a = approve(shop, sold["id"], body).json()["approval_id"]
    assert refund(shop, sold["id"], refund_body(shop, sold, qty=2), a).status_code == 403   # other amount
    assert refund(shop, sold["id"], refund_body(shop, sold, reason="muu põhjus"), a).status_code == 403
    world.clock.at += auth.APPROVAL_TTL + timedelta(seconds=1)
    assert refund(shop, sold["id"], body, a).status_code == 403                               # expired
    with world.app.state.session_factory() as s:
        assert s.scalar(select(Refund.id)) is None


def test_approval_cannot_be_used_from_another_session(shop, sold, world):
    body = refund_body(shop, sold)
    a = approve(shop, sold["id"], body).json()["approval_id"]
    other = world.till(shop["dev"], shop["cashier"], "4829")
    assert refund(shop, sold["id"], body, a, client=other).status_code == 403


def test_drawer_variance_needs_approval_for_cashier(shop):
    till = shop["till"]
    d = open_drawer(till, shop["reg"])
    r = till.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 4900})
    assert r.status_code == 403 and r.json()["error"] == "approval_required"
    assert till.get(f"/api/v1/registers/{shop['reg']}/drawer").json()["drawer"]["status"] == "open"
    a = till.post("/api/v1/auth/approvals", {"action": "drawer_close", "approver_id": shop["manager"],
                                              "pin": "7351", "request": {"drawer_id": d["id"], "counted_cents": 4900}})
    assert a.status_code == 201
    r = till.post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 4900},
                  {"X-Approval-Id": a.json()["approval_id"]})
    assert r.status_code == 200 and r.json()["variance_cents"] == -100


def test_cashier_closes_without_approval_when_count_matches(shop):
    d = open_drawer(shop["till"], shop["reg"])
    assert shop["till"].post(f"/api/v1/drawer/{d['id']}/close", {"counted_cents": 5000}).status_code == 200


# ------------------------------------------------------------- audit


def test_auth_events_are_recorded_and_append_only(shop, world):
    world.c.post("/api/v1/auth/pin-login", json={"staff_id": shop["cashier"], "pin": "0000"},
                 headers={"X-Device-Token": shop["dev"]})
    events = shop["adm"].get("/api/v1/admin/auth-events").json()
    kinds = {(e["event"], e["outcome"]) for e in events}
    assert {("login.pin", "fail"), ("login.pin", "ok"), ("device.enroll", "ok"), ("staff.create", "ok"),
            ("login.password", "ok")} <= kinds
    from axar_pos.models import AppendOnlyViolation

    with world.app.state.session_factory() as s:
        ev = s.scalar(select(AuthEvent))
        ev.outcome = "tampered"
        with pytest.raises(AppendOnlyViolation):
            s.flush()


def test_pin_is_never_returned_or_logged(shop, world):
    staff = shop["adm"].get("/api/v1/admin/staff").json()
    assert all("pin" not in x and "pin_hash" not in x for x in staff)
    with world.app.state.session_factory() as s:
        details = " ".join(e.detail or "" for e in s.scalars(select(AuthEvent)))
    # Random UUIDs (device/session ids) can contain any 4 digits by chance;
    # remove them so a hit really means a logged PIN (was a rare false failure).
    import re

    details = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", details)
    assert "4829" not in details and "7351" not in details


def test_inactive_staff_is_refused_even_if_a_session_was_not_revoked(shop, world):
    """Defence in depth: the per-request check of staff.active, independent of
    session revocation (e.g. a deactivation done directly in the database)."""
    with world.app.state.session_factory() as s:
        s.get(Staff, shop["cashier"]).active = False
        s.commit()
    assert shop["till"].get("/api/v1/products").status_code == 401


def test_rls_hides_other_tenants_staff_on_postgres(shop, world):
    from sqlalchemy import text

    if world.app.state.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL only")
    with world.app.state.engine.connect() as c:
        c.execute(text("SELECT set_config('app.tenant_id', 't-b', true)"))
        assert {r for (r,) in c.execute(text("SELECT tenant_id FROM staff"))} == {"t-b"}
        assert c.scalar(text("SELECT count(*) FROM staff_roles WHERE tenant_id = 't-a'")) == 0


# ------------------------------------------------- concurrency (AION review #21)


def test_concurrent_wrong_pins_are_all_counted(shop, world):
    """No lost updates: N parallel wrong PINs must count N failures on the
    device and on the staff member (real race on PostgreSQL)."""
    from concurrent.futures import ThreadPoolExecutor

    from axar_pos.models import Device

    n = 12
    world.clock.at = world.clock.at  # frozen clock: all attempts in the same window
    body = {"staff_id": shop["cashier"], "pin": "0000"}

    def attempt(_):
        return world.c.post("/api/v1/auth/pin-login", json=body, headers={"X-Device-Token": shop["dev"]}).status_code

    with ThreadPoolExecutor(n) as pool:
        codes = list(pool.map(attempt, range(n)))
    assert set(codes) <= {401, 429}
    with world.app.state.session_factory() as s:
        st = s.get(Staff, shop["cashier"])
        dev = s.scalar(select(Device).where(Device.token_hash == auth.token_hash(shop["dev"])))
    failed = codes.count(401)
    assert st.failed_pin_attempts == failed and dev.failed_pin_attempts == failed, (codes, st.failed_pin_attempts,
                                                                                     dev.failed_pin_attempts)
    assert st.pin_locked_until is not None and dev.pin_locked_until is not None
    # Parallel guesses cannot all slip past the lockout check: exactly
    # LOCK_AFTER are evaluated, the rest are refused as locked.
    assert failed == auth.LOCK_AFTER and codes.count(429) == n - auth.LOCK_AFTER, codes


def test_concurrent_wrong_passwords_lock_after_the_limit(world):
    from concurrent.futures import ThreadPoolExecutor

    n = 10
    body = {"tenant_id": "t-a", "login": "owner", "password": "wrong-password-xx", "totp": "123456"}
    with ThreadPoolExecutor(n) as pool:
        codes = list(pool.map(lambda _: world.c.post("/api/v1/auth/login", json=body).status_code, range(n)))
    assert codes.count(401) == auth.LOCK_AFTER and codes.count(429) == n - auth.LOCK_AFTER, codes
    with world.app.state.session_factory() as s:
        st = s.scalar(select(Staff).where(Staff.tenant_id == "t-a", Staff.login == "owner"))
    assert st.failed_login_attempts == auth.LOCK_AFTER


@pytest.mark.parametrize("kw,reason", [
    ({"demo_identity": True}, "demo identity"),
    ({"database_url": "sqlite:///x.db"}, "not PostgreSQL"),
    ({"admin_database_url": None}, "same"),
    ({"secret_key": None}, "AXAR_POS_SECRET_KEY"),
])
def test_pilot_profile_refuses_unsafe_configuration(kw, reason):
    good = dict(database_url="postgresql+psycopg://axar_app@db/axar", admin_database_url="postgresql+psycopg://owner@db/axar",
                secret_key=TEST_KEY, profile="pilot")
    assert Settings(**good).pilot_problems() == []
    with pytest.raises(RuntimeError, match=reason):
        create_app(Settings(**{**good, **kw}))


# ------------------------------------------------------ realtime stream scope


def _stream(cl, params="max_s=1&after=0"):
    frames = []
    with cl.c.stream("GET", f"/api/v1/events/stream?{params}", headers=cl.h) as r:
        if r.status_code != 200:
            return r.status_code, []
        cur = {}
        for line in r.iter_lines():
            if not line:
                if cur:
                    frames.append(cur)
                cur = {}
            elif ":" in line and not line.startswith(":"):
                k, v = line.split(":", 1)
                cur[k] = v.strip()
    return 200, frames


def test_open_or_reconnecting_stream_does_not_keep_an_idle_till_unlocked(shop, world, settings):
    """AION review #21 finding 2: the UI reconnects its stream every few
    minutes; that must not count as staff activity, or the till never locks."""
    settings.stream_poll_s = 0.05
    till = shop["till"]
    world.clock.at += timedelta(minutes=settings.till_idle_minutes - 5)
    assert _stream(till)[0] == 200                     # reconnect while the cashier is away
    world.clock.at += timedelta(minutes=6)             # idle since the last REAL request
    assert till.get("/api/v1/products").status_code == 401


def test_stream_of_location_manager_back_office_session_is_location_scoped(shop, world, settings):
    settings.stream_poll_s = 0.05
    loc1, loc2 = shop["loc"], world.ids[("t-a", "L2")]
    secret = shop["adm"].put(f"/api/v1/admin/staff/{shop['manager']}/credentials",
                             {"login": "mart", "password": MGR_PW}).json()["totp_secret"]
    mgr = world.admin(login="mart", password=MGR_PW, secret=secret)   # manager of L1 only
    cash2 = world.staff(shop["adm"], "Liis", [("cashier", loc2)], "6183")
    till2 = world.till(world.device(shop["adm"], loc="L2"), cash2, "6183")
    open_drawer(till2, world.ids[("t-a", "L2", "R1")])                # event at L2
    open_drawer(shop["till"], shop["reg"])                            # event at L1
    _, frames = _stream(mgr)
    locs = {json.loads(f["data"]).get("location_id") for f in frames if f.get("id")}
    assert locs == {loc1}, locs
    _, owner_frames = _stream(shop["adm"])
    assert {json.loads(f["data"]).get("location_id") for f in owner_frames if f.get("id")} == {loc1, loc2}


# ------------------------------------------------------ offline sync (O1b)


def test_sync_push_only_for_the_devices_own_journal_and_quarantine_needs_a_manager(shop):
    me = shop["till"].get("/api/v1/auth/device").json()["device"]
    entry = {"id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "seq": 1, "stream": "device:x", "type": "sale", "entity": "e",
             "summary": "0" * 64, "at": "2026-03-10T10:00:00+00:00", "prev_hash": "0" * 64, "hash": "0" * 64}
    body = {"device_id": "someone-elses-device", "entries": [{"entry": entry, "payload": "{}",
                                                               "payload_hash": "0" * 64}]}
    r = shop["till"].post("/api/v1/sync/push", body)
    assert r.status_code == 403 and "own journal" in r.json()["message"]
    body["device_id"] = me["id"]
    r = shop["till"].post("/api/v1/sync/push", body)                 # authorised; this entry is invalid
    assert r.status_code == 200 and r.json()["results"][0]["status"] == "chain_broken"
    assert shop["till"].get("/api/v1/sync/quarantine").status_code == 403        # cashier
    assert shop["adm"].get("/api/v1/sync/quarantine").status_code == 200         # owner back office


def test_offline_limit_setting_needs_an_owner_or_tenant_wide_manager(shop, world):
    """POS settings apply to every location: a cashier till and a manager of one
    location may read nothing / change nothing; the owner may (audited)."""
    body = {"offline_limit_hours": 24}
    assert shop["till"].put("/api/v1/admin/pos-settings", body).status_code in (401, 403)
    secret = shop["adm"].put(f"/api/v1/admin/staff/{shop['manager']}/credentials",
                             {"login": "mart", "password": MGR_PW}).json()["totp_secret"]
    mgr = world.admin(login="mart", password=MGR_PW, secret=secret)   # manager of L1 only
    assert mgr.put("/api/v1/admin/pos-settings", body).status_code == 403
    r = shop["adm"].put("/api/v1/admin/pos-settings", body)
    assert r.status_code == 200 and r.json()["offline_limit_hours"] == 24
    assert shop["adm"].get("/api/v1/admin/pos-settings").json()["offline_limit_hours"] == 24
