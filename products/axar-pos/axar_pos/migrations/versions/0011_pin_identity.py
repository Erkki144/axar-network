"""PIN identity and register grants (#81).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-27

* staff.pin_index: blind index "<kid>:" + HMAC-SHA256(server pepper, tenant | PIN),
  UNIQUE per tenant. Lets PIN-only login find exactly one staff member
  without trying every Argon2 hash; the database, not the code, guarantees
  that two active staff members of one tenant never share a PIN.
* Existing PINs stay Argon2-only (pin_index NULL): a hash cannot be turned
  back into the PIN. They get their index on the next successful login with
  the name grid, or when the office sets / generates a new PIN
  (docs/runbooks/PIN_INDEX_KEY_ROTATION.md).
* staff_register_grants: the tills a staff member may work on. No rows =
  every till at their role locations (pre-#81 behaviour).
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

APP_ROLE = "axar_app"


def upgrade() -> None:
    op.add_column("staff", sa.Column("pin_index", sa.String(80), nullable=True))
    op.create_unique_constraint("uq_staff_pin_index", "staff", ["tenant_id", "pin_index"])
    op.create_table(
        "staff_register_grants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("staff_id", sa.String(36), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("register_id", sa.Integer(), sa.ForeignKey("registers.id"), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("staff_id", "register_id", name="uq_staff_register_grant"),
    )
    op.create_index("ix_staff_register_grants_tenant_id", "staff_register_grants", ["tenant_id"])
    op.create_index("ix_staff_register_grants_staff_id", "staff_register_grants", ["staff_id"])
    if op.get_bind().dialect.name == "postgresql":
        policy = "tenant_id = current_setting('app.tenant_id', true)"
        op.execute("ALTER TABLE staff_register_grants ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON staff_register_grants USING ({policy}) WITH CHECK ({policy})")
        op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') "
                   f"THEN GRANT SELECT, INSERT, DELETE ON staff_register_grants TO {APP_ROLE}; "
                   f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}; END IF; END $$")


def downgrade() -> None:
    op.drop_index("ix_staff_register_grants_staff_id", table_name="staff_register_grants")
    op.drop_index("ix_staff_register_grants_tenant_id", table_name="staff_register_grants")
    op.drop_table("staff_register_grants")
    op.drop_constraint("uq_staff_pin_index", "staff", type_="unique")
    op.drop_column("staff", "pin_index")
