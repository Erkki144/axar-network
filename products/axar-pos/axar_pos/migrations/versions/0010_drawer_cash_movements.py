"""Drawer non-sale cash movements (#68).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-26

Append-only records of cash added or removed from a till outside sales.
The cached drawer totals are guarded by the movement service and a
reconciliation test. PostgreSQL app role has only SELECT/INSERT on movement
records. Existing drawer data gets zero defaults.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drawer_sessions", sa.Column("cash_in_cents", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("drawer_sessions", sa.Column("cash_out_cents", sa.BigInteger(), nullable=False, server_default="0"))
    op.create_table(
        "drawer_cash_movements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("register_id", sa.Integer(), sa.ForeignKey("registers.id"), nullable=False),
        sa.Column("drawer_session_id", sa.Integer(), sa.ForeignKey("drawer_sessions.id"), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("reason_text", sa.String(300), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("approval_id", sa.String(36), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_drawer_cash_move_key"),
        sa.UniqueConstraint("tenant_id", "sequence_no", name="uq_drawer_cash_move_seq"),
    )
    op.create_index("ix_drawer_cash_movements_tenant_id", "drawer_cash_movements", ["tenant_id"])
    op.create_index("ix_drawer_cash_movements_register_id", "drawer_cash_movements", ["register_id"])
    op.create_index("ix_drawer_cash_movements_drawer_session_id", "drawer_cash_movements", ["drawer_session_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE drawer_cash_movements ENABLE ROW LEVEL SECURITY")
        policy = "tenant_id = current_setting('app.tenant_id', true)"
        op.execute(f"CREATE POLICY tenant_isolation ON drawer_cash_movements USING ({policy}) WITH CHECK ({policy})")
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='axar_app') THEN
              GRANT SELECT, INSERT ON drawer_cash_movements TO axar_app;
            END IF;
          END $$""")

def downgrade() -> None:
    op.drop_index("ix_drawer_cash_movements_drawer_session_id", table_name="drawer_cash_movements")
    op.drop_index("ix_drawer_cash_movements_register_id", table_name="drawer_cash_movements")
    op.drop_index("ix_drawer_cash_movements_tenant_id", table_name="drawer_cash_movements")
    op.drop_table("drawer_cash_movements")
    op.drop_column("drawer_sessions", "cash_out_cents")
    op.drop_column("drawer_sessions", "cash_in_cents")
