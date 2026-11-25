"""offline limit

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-25

Offline-first O1c (ADR-0012 §6): central pos_settings (offline_limit_hours,
owner decision: at most 72 h, set in the POS settings) and the edge's own
edge_sync_state (last contact, clock high-water mark). RLS + grants.
"""

from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

APP_ROLE = "axar_app"


def upgrade() -> None:
    op.create_table('pos_settings',
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('offline_limit_hours', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('tenant_id')
    )
    op.create_table('edge_sync_state',
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('last_contact_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('max_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('offline_limit_hours', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('tenant_id', 'device_id')
    )
    if op.get_bind().dialect.name == "postgresql":
        policy = "tenant_id = current_setting('app.tenant_id', true)"
        body = []
        for table in ("pos_settings", "edge_sync_state"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({policy}) WITH CHECK ({policy})")
            body.append(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")
        op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') "
                   f"THEN {'; '.join(body)}; END IF; END $$")


def downgrade() -> None:
    op.drop_table('edge_sync_state')
    op.drop_table('pos_settings')
