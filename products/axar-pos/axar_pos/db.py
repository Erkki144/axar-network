from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base, Location, Product, Register, TaxRate, Tenant

# Estonian VAT. Standard rate rose 22 % -> 24 % on 2025-07-01.
# Must be re-verified with an accountant before any real use.
EE_TAX_RATES = [
    ("standard", 2200, date(2024, 1, 1), date(2025, 6, 30)),
    ("standard", 2400, date(2025, 7, 1), None),
    ("reduced", 900, date(2024, 1, 1), None),
    ("zero", 0, date(2024, 1, 1), None),
]


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        # pysqlite's implicit BEGIN is DEFERRED: two transactions can both read
        # "drawer open" and then race to upgrade to a write lock (SQLITE_BUSY /
        # lost update). We take over transaction control and start every
        # transaction with BEGIN IMMEDIATE so writers are serialized from
        # their first read. Correctness does NOT rely on this alone: the sale
        # path also uses a conditional UPDATE ... WHERE status='open', which is
        # what protects PostgreSQL (row lock + READ COMMITTED re-check).

        @event.listens_for(engine, "connect")
        def _sqlite_connect(dbapi_conn, _rec):
            dbapi_conn.isolation_level = None  # disable pysqlite's own BEGIN
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=10000")
            cur.close()

        @event.listens_for(engine, "begin")
        def _sqlite_begin(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def init_db(engine: Engine) -> sessionmaker[Session]:
    # Slice 1 uses create_all; Alembic migrations arrive with PostgreSQL (#2).
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as s:
        if s.scalar(select(TaxRate.id).limit(1)) is None:
            for cat, bp, start, end in EE_TAX_RATES:
                s.add(TaxRate(category=cat, rate_bp=bp, valid_from=start, valid_to=end))
    return factory


DEMO_PRODUCTS = [
    ("KOHV", "Kohv", 250, "standard"),
    ("LATTE", "Latte", 380, "standard"),
    ("TEE", "Tee", 200, "standard"),
    ("SAIAKE", "Kaneelisaiake", 295, "standard"),
    ("VESI", "Mineraalvesi 0,5 l", 180, "standard"),
    ("RAAMAT", "Raamat", 1599, "reduced"),
]


def seed_demo(factory: sessionmaker[Session], tenant_id: str = "demo") -> None:
    with factory.begin() as s:
        if s.get(Tenant, tenant_id) is None:
            s.add(Tenant(id=tenant_id, name="Demo kohvik"))
            s.flush()
            loc = Location(tenant_id=tenant_id, code="POOD1", name="Kesklinna kohvik")
            s.add(loc)
            s.flush()
            s.add_all([
                Register(tenant_id=tenant_id, location_id=loc.id, code="KASSA1", name="Kassa 1"),
                Register(tenant_id=tenant_id, location_id=loc.id, code="KASSA2", name="Kassa 2"),
            ])
            for sku, name, price, cat in DEMO_PRODUCTS:
                s.add(Product(tenant_id=tenant_id, sku=sku, name=name,
                              price_cents=price, tax_category=cat))
