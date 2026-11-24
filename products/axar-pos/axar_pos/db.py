from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base, DiningTable, Location, Product, Register, TaxRate, Tenant

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


MIGRATIONS = Path(__file__).parent / "migrations"


def migrate(engine: Engine) -> None:
    """PostgreSQL: Alembic ``upgrade head`` (owner connection). SQLite (local
    demo, fast tests): ``create_all`` — RLS and grants do not exist there."""
    if engine.dialect.name != "postgresql":
        Base.metadata.create_all(engine)
        return
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def init_db(engine: Engine) -> sessionmaker[Session]:
    """Migrate with the ADMIN (owner) engine and seed reference data."""
    migrate(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as s:
        if s.scalar(select(TaxRate.id).limit(1)) is None:
            for cat, bp, start, end in EE_TAX_RATES:
                s.add(TaxRate(category=cat, rate_bp=bp, valid_from=start, valid_to=end))
    return factory


def request_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions used by API requests. Once ``bind_tenant`` was called, every
    transaction of the session starts with ``SET LOCAL app.tenant_id`` so
    PostgreSQL row-level security confines it to that tenant."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @event.listens_for(factory, "after_begin")
    def _set_tenant(session, _transaction, connection):
        tenant = session.info.get("tenant_id")
        if tenant and connection.dialect.name == "postgresql":
            connection.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})

    return factory


def bind_tenant(s: Session, tenant_id: str) -> None:
    s.info["tenant_id"] = tenant_id
    if s.in_transaction() and s.get_bind().dialect.name == "postgresql":
        s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})


DEMO_TABLES = [(f"L{i:02d}", f"Laud {i:02d}") for i in range(1, 5)] + \
    [(f"T{i:02d}", f"Terrass {i:02d}") for i in range(1, 5)]

DEMO_PRODUCTS = [  # sku, name, price (cents, VAT incl.), tax category, menu category
    ("KOHV", "Kohv", 250, "standard", "Kohv"),
    ("LATTE", "Latte", 380, "standard", "Kohv"),
    ("CAPPU", "Cappuccino", 330, "standard", "Kohv"),
    ("TEE", "Tee", 200, "standard", "Kohv"),
    ("SAIAKE", "Kaneelisaiake", 295, "standard", "Toit"),
    ("CROISSANT", "Võicroissant", 290, "standard", "Toit"),
    ("SUPP", "Päevasupp", 590, "standard", "Toit"),
    ("VOILEIB", "Võileib", 499, "standard", "Toit"),
    ("VESI", "Mineraalvesi 0,5 l", 180, "standard", "Joogid"),
    ("MAHL", "Õunamahl", 271, "standard", "Joogid"),
    ("RAAMAT", "Raamat", 1599, "reduced", "Muud"),
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
            s.add_all(DiningTable(tenant_id=tenant_id, location_id=loc.id, code=code, name=name)
                      for code, name in DEMO_TABLES)
            for sku, name, price, cat, menu in DEMO_PRODUCTS:
                s.add(Product(tenant_id=tenant_id, sku=sku, name=name,
                              price_cents=price, tax_category=cat, category=menu))
