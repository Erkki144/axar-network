"""catalog and pricing (P1-1a)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-26

Product group tree, product card fields, barcodes, outlet price lists and
four-eyes price versions (ADR-0015, docs/discovery/catalog.md §8). Additive
only: existing products keep ``price_cents`` as their legacy price until a
base price version is approved. RLS + grants like 0008.
"""

from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

APP_ROLE = "axar_app"

CATALOG_TABLES = {
    "product_groups": "SELECT, INSERT, UPDATE",
    "product_barcodes": "SELECT, INSERT, DELETE",
    "price_lists": "SELECT, INSERT, UPDATE",
    "price_versions": "SELECT, INSERT, UPDATE",  # status changes only (draft -> decided)
}


def upgrade() -> None:
    op.create_table('product_groups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['product_groups.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_product_groups_tenant_id'), 'product_groups', ['tenant_id'], unique=False)
    op.create_table('price_lists',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'name', name='uq_price_list_name')
    )
    op.create_index(op.f('ix_price_lists_tenant_id'), 'price_lists', ['tenant_id'], unique=False)
    op.add_column('locations', sa.Column('price_list_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_locations_price_list', 'locations', 'price_lists', ['price_list_id'], ['id'])
    op.add_column('products', sa.Column('group_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_products_group', 'products', 'product_groups', ['group_id'], ['id'])
    op.add_column('products', sa.Column('short_name', sa.String(length=40), nullable=True))
    op.add_column('products', sa.Column('kind', sa.String(length=16), server_default='standard', nullable=False))
    op.add_column('products', sa.Column('hidden_at_till', sa.Boolean(), server_default=sa.text('false'),
                                        nullable=False))
    op.add_column('products', sa.Column('description', sa.String(length=500), nullable=True))
    op.add_column('products', sa.Column('version', sa.Integer(), server_default='1', nullable=False))
    op.create_table('product_barcodes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'code', name='uq_barcode_code')
    )
    op.create_index(op.f('ix_product_barcodes_product_id'), 'product_barcodes', ['product_id'], unique=False)
    op.create_index(op.f('ix_product_barcodes_tenant_id'), 'product_barcodes', ['tenant_id'], unique=False)
    op.create_table('price_versions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('price_list_id', sa.Integer(), nullable=True),
    sa.Column('price_cents', sa.BigInteger(), nullable=True),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_by', sa.String(length=64), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.String(length=200), nullable=True),
    sa.ForeignKeyConstraint(['price_list_id'], ['price_lists.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_price_versions_lookup', 'price_versions',
                    ['tenant_id', 'product_id', 'price_list_id', 'status', 'valid_from'], unique=False)
    op.create_index(op.f('ix_price_versions_product_id'), 'price_versions', ['product_id'], unique=False)
    op.create_index(op.f('ix_price_versions_tenant_id'), 'price_versions', ['tenant_id'], unique=False)
    secure_catalog_tables()


def secure_catalog_tables() -> None:
    policy = "tenant_id = current_setting('app.tenant_id', true)"
    grants = []
    for table, privs in CATALOG_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({policy}) WITH CHECK ({policy})")
        grants.append(f"GRANT {privs} ON {table} TO {APP_ROLE}")
    grants.append(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    body = "; ".join(grants)
    op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') "
               f"THEN {body}; END IF; END $$")


def downgrade() -> None:
    op.drop_index(op.f('ix_price_versions_tenant_id'), table_name='price_versions')
    op.drop_index(op.f('ix_price_versions_product_id'), table_name='price_versions')
    op.drop_index('ix_price_versions_lookup', table_name='price_versions')
    op.drop_table('price_versions')
    op.drop_index(op.f('ix_product_barcodes_tenant_id'), table_name='product_barcodes')
    op.drop_index(op.f('ix_product_barcodes_product_id'), table_name='product_barcodes')
    op.drop_table('product_barcodes')
    op.drop_constraint('fk_products_group', 'products', type_='foreignkey')
    for col in ('version', 'description', 'hidden_at_till', 'kind', 'short_name', 'group_id'):
        op.drop_column('products', col)
    op.drop_constraint('fk_locations_price_list', 'locations', type_='foreignkey')
    op.drop_column('locations', 'price_list_id')
    op.drop_index(op.f('ix_price_lists_tenant_id'), table_name='price_lists')
    op.drop_table('price_lists')
    op.drop_index(op.f('ix_product_groups_tenant_id'), table_name='product_groups')
    op.drop_table('product_groups')
