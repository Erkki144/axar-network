"""Alembic environment. Migrations run with the OWNER (admin) connection; the
application itself connects as the non-owner role ``axar_app`` so PostgreSQL
row-level security applies to it."""

from alembic import context
from sqlalchemy import create_engine, pool

from axar_pos.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.attributes.get("url") or config.get_main_option("sqlalchemy.url")
    if not url:
        import os

        url = os.environ.get("AXAR_POS_ADMIN_DATABASE_URL") or os.environ["AXAR_POS_DATABASE_URL"]
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
