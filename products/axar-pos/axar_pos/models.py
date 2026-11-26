"""Persistence model.

Every business table carries ``tenant_id``. Sale, SaleLine and OutboxEvent are
append-only: an ORM guard refuses UPDATE/DELETE on them (ARCHITECTURE.md #4 —
corrections must be new auditable records, never edits).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC timestamps on every backend.

    SQLite drops tzinfo on round-trip; a naive value read back would then be
    interpreted in the *host's* local zone by ``astimezone()``. We store naive
    UTC and always return aware UTC. Naive input is rejected.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        # PostgreSQL: real ``timestamptz``. SQLite: naive UTC text.
        return dialect.type_descriptor(DateTime(timezone=dialect.name == "postgresql"))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; pass an aware UTC datetime")
        value = value.astimezone(timezone.utc)
        return value if dialect.name == "postgresql" else value.replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime}


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class TenantCounter(Base):
    """Per-tenant document numbers (receipts, refunds, kitchen tickets).

    Taken with one ``INSERT … ON CONFLICT DO UPDATE … RETURNING`` statement:
    on PostgreSQL the row lock is held until commit, so numbers are unique and
    gap-free (a failed transaction rolls its number back) without ``max()+1``.
    """

    __tablename__ = "tenant_counters"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    # P1-1a: the outlet's price list. NULL = base prices only (catalog.price_for).
    price_list_id: Mapped[int | None] = mapped_column(ForeignKey("price_lists.id"), nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code"),)


class Register(Base):
    """A physical till. Each register has its own cash drawer sessions."""

    __tablename__ = "registers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(default=True)
    # Per-register receipt series (ADR-0012 §3), set by a manager in settings.
    # NULL prefix = labels off (receipts show the tenant number as before);
    # the per-register number is always assigned (offline tills need it).
    receipt_prefix: Mapped[str | None] = mapped_column(String(8), nullable=True)
    receipt_floor: Mapped[int] = mapped_column(Integer, default=1, server_default="1")  # next number >= floor

    location: Mapped[Location] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("tenant_id", "code"),)


class TaxRate(Base):
    """Versioned VAT rates (jurisdiction-wide, not tenant data)."""

    __tablename__ = "tax_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(32))
    rate_bp: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)  # inclusive

    __table_args__ = (UniqueConstraint("category", "valid_from"),)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    price_cents: Mapped[int] = mapped_column(BigInteger)  # VAT-inclusive
    tax_category: Mapped[str] = mapped_column(String(32), default="standard")
    # Menu grouping for the cashier UI only; no financial meaning.
    category: Mapped[str] = mapped_column(String(64), default="Muud")
    active: Mapped[bool] = mapped_column(default=True)
    # What a sale of this menu offer consumes from stock (#14):
    # none | recipe (ingredients, made to order) | item (finished/resale stock).
    stock_mode: Mapped[str] = mapped_column(String(8), default="none", server_default="none")
    stock_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"), nullable=True)
    # P1-1a product card (docs/discovery/catalog.md §2). ``price_cents`` above is
    # the LEGACY price: used only while a product has no approved base version.
    group_id: Mapped[int | None] = mapped_column(ForeignKey("product_groups.id"), nullable=True)
    short_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="standard", server_default="standard")
    hidden_at_till: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (UniqueConstraint("tenant_id", "sku"),)


class ProductGroup(Base):
    """Product group tree (P1-1a). Names a branch; carries defaults later."""

    __tablename__ = "product_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("product_groups.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(default=True)


class ProductBarcode(Base):
    __tablename__ = "product_barcodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    code: Mapped[str] = mapped_column(String(32))

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_barcode_code"),)


class PriceList(Base):
    """An outlet price list; outlets point at it (``Location.price_list_id``)."""

    __tablename__ = "price_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_price_list_name"),)


class PriceVersion(Base):
    """One proposed or published price (ADR-0016). Never edited once decided:
    draft -> approved | rejected | withdrawn. A new price is a new row.

    ``price_list_id`` NULL = the product's base price (all outlets without an
    override). ``price_cents`` NULL = "not sold" in that price list (only
    allowed on a list, never on the base). Four eyes: ``approved_by`` must
    differ from ``created_by`` (catalog.approve_price)."""

    __tablename__ = "price_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    price_list_id: Mapped[int | None] = mapped_column(ForeignKey("price_lists.id"), nullable=True)
    price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # VAT-inclusive
    valid_from: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(12), default="draft")
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        Index("ix_price_versions_lookup", "tenant_id", "product_id", "price_list_id", "status", "valid_from"),
    )


class DrawerSession(Base):
    __tablename__ = "drawer_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    register_id: Mapped[int] = mapped_column(ForeignKey("registers.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|closed
    opened_by: Mapped[str] = mapped_column(String(64))
    opened_at: Mapped[datetime]
    opening_float_cents: Mapped[int] = mapped_column(BigInteger)
    # Running totals, changed ONLY by a conditional UPDATE ... WHERE
    # status='open' inside the sale transaction (see services._claim_drawer).
    cash_sales_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    sale_count: Mapped[int] = mapped_column(Integer, default=0)
    cash_refunds_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    refund_count: Mapped[int] = mapped_column(Integer, default=0)
    closed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expected_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    counted_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    variance_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    register: Mapped[Register] = relationship(lazy="joined")

    __table_args__ = (
        # At most one open drawer per register, enforced by the database.
        Index(
            "uq_one_open_drawer_per_register",
            "register_id",
            unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
    )


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    receipt_no: Mapped[int] = mapped_column(Integer)
    register_id: Mapped[int] = mapped_column(ForeignKey("registers.id"), index=True)
    drawer_session_id: Mapped[int] = mapped_column(ForeignKey("drawer_sessions.id"), index=True)
    cashier_id: Mapped[str] = mapped_column(String(64))
    # Who/what acted (server auth, RFC-0002); NULL for demo-identity records.
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime]
    business_date: Mapped[date] = mapped_column(Date, index=True)
    payment_method: Mapped[str] = mapped_column(String(16), default="cash")
    net_cents: Mapped[int] = mapped_column(BigInteger)
    vat_cents: Mapped[int] = mapped_column(BigInteger)
    total_cents: Mapped[int] = mapped_column(BigInteger)  # exact gross of all lines
    cash_rounding_cents: Mapped[int] = mapped_column(BigInteger)  # cash_due - total
    cash_due_cents: Mapped[int] = mapped_column(BigInteger)
    tendered_cents: Mapped[int] = mapped_column(BigInteger)
    change_cents: Mapped[int] = mapped_column(BigInteger)
    # Per-register series (gap-free per register); NULL on sales made before 0005.
    register_receipt_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receipt_label: Mapped[str | None] = mapped_column(String(24), nullable=True)  # e.g. K1-000123

    lines: Mapped[list["SaleLine"]] = relationship(
        back_populates="sale", order_by="SaleLine.line_no", lazy="selectin"
    )
    register: Mapped[Register] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_sale_idempotency"),
        UniqueConstraint("tenant_id", "receipt_no", name="uq_sale_receipt_no"),
        UniqueConstraint("tenant_id", "register_id", "register_receipt_no", name="uq_sale_register_receipt"),
    )


class SaleLine(Base):
    """Immutable snapshot of product, price and tax at time of sale."""

    __tablename__ = "sale_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    unit_price_cents: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[int] = mapped_column(Integer)
    gross_cents: Mapped[int] = mapped_column(BigInteger)
    tax_category: Mapped[str] = mapped_column(String(32))
    vat_rate_bp: Mapped[int] = mapped_column(Integer)
    vat_cents: Mapped[int] = mapped_column(BigInteger)
    net_cents: Mapped[int] = mapped_column(BigInteger)

    sale: Mapped[Sale] = relationship(back_populates="lines")


class RefundAllocation(Base):
    """Mutable per-sale-line refund counter (one row per sale line, created
    with the sale). Changed only by a conditional UPDATE that refuses to go
    above the original quantity — the over-refund guard under concurrency."""

    __tablename__ = "refund_allocations"

    sale_line_id: Mapped[int] = mapped_column(ForeignKey("sale_lines.id"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True)
    original_quantity: Mapped[int] = mapped_column(Integer)
    refunded_quantity: Mapped[int] = mapped_column(Integer, default=0)
    refunded_gross_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    refunded_vat_cents: Mapped[int] = mapped_column(BigInteger, default=0)


class SaleRefundState(Base):
    """Mutable per-sale refund totals (one row per sale, created with it).

    Cash refunds are rounded *cumulatively* per sale:
    ``cash_paid = round_cash(refunded_gross_after) - refunded_cash_before``,
    so after a full refund Σ cash paid == the sale's cash_due regardless of how
    the refund was split. Changed only by compare-and-swap.
    """

    __tablename__ = "sale_refund_state"

    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    refunded_gross_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    refunded_cash_cents: Mapped[int] = mapped_column(BigInteger, default=0)


class Refund(Base):
    """Simulated cash refund: a separate, append-only record that references
    the original sale. The original sale is never modified."""

    __tablename__ = "refunds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    refund_no: Mapped[int] = mapped_column(Integer)
    original_sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True)
    register_id: Mapped[int] = mapped_column(ForeignKey("registers.id"), index=True)
    drawer_session_id: Mapped[int] = mapped_column(ForeignKey("drawer_sessions.id"), index=True)
    cashier_id: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(64))
    approver_role: Mapped[str] = mapped_column(String(32))
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reason: Mapped[str] = mapped_column(String(500))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime]
    business_date: Mapped[date] = mapped_column(Date, index=True)
    payment_method: Mapped[str] = mapped_column(String(16), default="cash")
    # All amounts positive = money going back to the customer.
    total_cents: Mapped[int] = mapped_column(BigInteger)
    vat_cents: Mapped[int] = mapped_column(BigInteger)
    net_cents: Mapped[int] = mapped_column(BigInteger)
    cash_rounding_cents: Mapped[int] = mapped_column(BigInteger)
    cash_paid_cents: Mapped[int] = mapped_column(BigInteger)

    lines: Mapped[list["RefundLine"]] = relationship(
        back_populates="refund", order_by="RefundLine.line_no", lazy="selectin"
    )
    original_sale: Mapped[Sale] = relationship(lazy="selectin")
    register: Mapped[Register] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_refund_idempotency"),
        UniqueConstraint("tenant_id", "refund_no", name="uq_refund_no"),
    )


class RefundLine(Base):
    __tablename__ = "refund_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    refund_id: Mapped[str] = mapped_column(ForeignKey("refunds.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    sale_line_id: Mapped[int] = mapped_column(ForeignKey("sale_lines.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    unit_price_cents: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[int] = mapped_column(Integer)
    gross_cents: Mapped[int] = mapped_column(BigInteger)
    vat_rate_bp: Mapped[int] = mapped_column(Integer)  # from the original line
    vat_cents: Mapped[int] = mapped_column(BigInteger)
    net_cents: Mapped[int] = mapped_column(BigInteger)

    refund: Mapped[Refund] = relationship(back_populates="lines")


class OutboxEvent(Base):
    """Transactional outbox (ADR-0004); written in the same transaction as the
    state change it describes.

    ``version`` is the PAYLOAD schema version of ``type``. The envelope-v2
    columns (aggregate, sequence, causation, actor, location) are filled for
    every event. ``recorded_seq`` is a gap-free per-tenant sequence assigned
    in the last step before commit (``events.py``), so sequence order equals
    commit order within a tenant: a reader that has seen ``seq = n`` can never
    later find an event ``< n`` appear. It is the SSE / consumer cursor.
    """

    __tablename__ = "outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    type: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime]
    correlation_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[str] = mapped_column(Text)  # canonical JSON
    payload_hash: Mapped[str] = mapped_column(String(64))
    recorded_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    aggregate_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    aggregate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    aggregate_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor: Mapped[str | None] = mapped_column(Text, nullable=True)  # canonical JSON
    location_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "recorded_seq", name="uq_outbox_seq"),)


class EventConsumer(Base):
    """Cursor + lease of one consumer for one tenant (ADR-0004)."""

    __tablename__ = "event_consumers"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(nullable=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0)
    failing_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column(nullable=True)  # block policy: waits for operator
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True)


class InboxEntry(Base):
    """Dedup record: (consumer, event) was applied. Written in the SAME
    transaction as the consumer's effect, so a redelivery is a no-op."""

    __tablename__ = "event_inbox"

    consumer: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    recorded_seq: Mapped[int] = mapped_column(BigInteger)
    processed_at: Mapped[datetime]


class ExportQueue(Base):
    """Dedicated outbox for EXTERNAL delivery (e.g. accounting, AXAR CONNECT).

    Filled by the ``books.export`` consumer inside its transaction (no network
    call there). A separate delivery step sends a row, and it is marked
    ``acknowledged`` only with the receiver's verified receipt id.
    """

    __tablename__ = "export_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    destination: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(64))
    event_version: Mapped[int] = mapped_column(Integer)
    recorded_seq: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|acknowledged|failed
    receipt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime]

    __table_args__ = (UniqueConstraint("tenant_id", "destination", "event_id", name="uq_export_event"),)


class DeadLetter(Base):
    """An event a consumer could not apply after bounded retries. Visible to
    operators; replay is an explicit, audited command."""

    __tablename__ = "event_dead_letters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consumer: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(36))
    recorded_seq: Mapped[int] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(Integer)
    error: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)  # replayed|skipped

    __table_args__ = (UniqueConstraint("consumer", "event_id", name="uq_dead_letter"),)


# ------------------------------------------------------------ restaurant orders
# Server-side state for table service (issue #11 follow-up / slice 4). An open
# order is the bill of one table; it is mutable only through version-checked
# (optimistic) updates and becomes immutable once paid.


class DiningTable(Base):
    __tablename__ = "dining_tables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code"),)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    table_id: Mapped[int] = mapped_column(ForeignKey("dining_tables.id"), index=True)
    # open | partially_paid | paid | invoiced (closed; remainder is a receivable) | voided
    status: Mapped[str] = mapped_column(String(16), default="open")
    version: Mapped[int] = mapped_column(Integer, default=1)
    opened_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    sale_id: Mapped[str | None] = mapped_column(ForeignKey("sales.id"), nullable=True, unique=True)

    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", order_by="OrderLine.id", lazy="selectin"
    )
    table: Mapped[DiningTable] = relationship(lazy="selectin")

    __table_args__ = (
        # One open (or partially paid) bill per table, enforced by the database.
        Index("uq_one_open_order_per_table", "table_id", unique=True,
              sqlite_where=text("status IN ('open', 'partially_paid')"),
              postgresql_where=text("status IN ('open', 'partially_paid')")),
    )


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    sent_quantity: Mapped[int] = mapped_column(Integer, default=0)  # already sent to kitchen

    order: Mapped[Order] = relationship(back_populates="lines")

    __table_args__ = (UniqueConstraint("order_id", "product_id"),)


class KitchenTicket(Base):
    __tablename__ = "kitchen_tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    ticket_no: Mapped[int] = mapped_column(Integer)
    table_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="new")  # new|ready
    idempotency_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime]
    ready_at: Mapped[datetime | None] = mapped_column(nullable=True)

    lines: Mapped[list["KitchenTicketLine"]] = relationship(lazy="selectin", order_by="KitchenTicketLine.id")

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_ticket_idempotency"),
        UniqueConstraint("tenant_id", "ticket_no", name="uq_ticket_no"),
    )


class KitchenTicketLine(Base):
    __tablename__ = "kitchen_ticket_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("kitchen_tickets.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)


# ------------------------------------------------------------ payments (C3, #15)
# A sale (receipt) is NOT a payment. A bill (order) collects payments of any
# tender; when payments + issued invoices cover it, ONE immutable sale is
# issued. A bank transfer is a receivable until a bank line is matched.


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    sale_id: Mapped[str | None] = mapped_column(ForeignKey("sales.id"), nullable=True, index=True)
    invoice_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    method: Mapped[str] = mapped_column(String(8))           # cash | card | transfer
    # captured | pending (transfer awaiting bank, card awaiting terminal) |
    # unknown (terminal answer lost) | declined
    status: Mapped[str] = mapped_column(String(10))
    amount_cents: Mapped[int] = mapped_column(BigInteger)    # applied to the bill
    rounding_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # cash only, final part
    tendered_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    change_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    register_id: Mapped[int] = mapped_column(ForeignKey("registers.id"))
    drawer_session_id: Mapped[int | None] = mapped_column(ForeignKey("drawer_sessions.id"), nullable=True)
    provider_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_payment_idempotency"),)


class CustomerInvoice(Base):
    """Receivable for the part of a bill paid by bank transfer."""

    __tablename__ = "customer_invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    invoice_no: Mapped[int] = mapped_column(Integer)
    reference_no: Mapped[str] = mapped_column(String(20))    # Estonian viitenumber (7-3-1)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    sale_id: Mapped[str | None] = mapped_column(ForeignKey("sales.id"), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(200))
    customer_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    paid_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16))          # issued | partially_paid | paid
    due_date: Mapped[date] = mapped_column(Date)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]

    __table_args__ = (UniqueConstraint("tenant_id", "invoice_no", name="uq_customer_invoice_no"),
                      UniqueConstraint("tenant_id", "reference_no", name="uq_customer_invoice_ref"))


class BankTransaction(Base):
    """Imported bank statement line (append-only); matched by reference."""

    __tablename__ = "bank_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    bank_tx_id: Mapped[str] = mapped_column(String(64))
    booked_date: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    reference: Mapped[str] = mapped_column(String(35))
    payer: Mapped[str] = mapped_column(String(200))
    invoice_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    allocated_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(12))          # matched | partial | overpaid | unmatched
    created_at: Mapped[datetime]

    __table_args__ = (UniqueConstraint("tenant_id", "bank_tx_id", name="uq_bank_tx"),)


class MockTerminalTxn(Base):
    """SIMULATED card terminal/provider record (no real card processing).
    Keyed by our payment id: a repeated or reconciled request reads the same
    provider-side outcome, like a real provider's idempotent API."""

    __tablename__ = "mock_terminal_txns"

    payment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    result: Mapped[str] = mapped_column(String(10))          # approved | declined
    created_at: Mapped[datetime]


# ------------------------------------------------------------ inventory (#14)
# Quantities are integers in MICRO base units (1 kg = 1_000_000) — exact, no
# floats. Values are integer euro cents (net, recoverable VAT excluded).
# Stock movements are append-only; balances are a locked running total that
# reconciliation proves equal to the sum of movements.

QTY_SCALE = 1_000_000


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    is_default: Mapped[bool] = mapped_column(default=False)  # where sales at this location consume

    __table_args__ = (UniqueConstraint("tenant_id", "code"),)


class Item(Base):
    """Stock-keeping item (ingredient, finished, resale, packaging). NOT a menu
    offer: menu offers are ``products`` and point here via recipe or item."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # ingredient|finished|resale|packaging
    uom: Mapped[str] = mapped_column(String(8))    # kg|l|pcs (base unit)
    active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (UniqueConstraint("tenant_id", "sku"),)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))

    __table_args__ = (UniqueConstraint("tenant_id", "code"),)


class SupplierDocument(Base):
    """Delivery note (saateleht) or purchase invoice. draft -> posted.
    Unique per (tenant, supplier, type, number): a document cannot be posted twice."""

    __tablename__ = "supplier_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    doc_type: Mapped[str] = mapped_column(String(16))  # delivery_note|invoice
    doc_no: Mapped[str] = mapped_column(String(64))
    doc_date: Mapped[date] = mapped_column(Date)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    matches_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # invoice -> delivery note
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|posted
    freight_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    posted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    lines: Mapped[list["SupplierDocumentLine"]] = relationship(lazy="selectin", order_by="SupplierDocumentLine.line_no")

    __table_args__ = (UniqueConstraint("tenant_id", "supplier_id", "doc_type", "doc_no", name="uq_supplier_document"),)


class SupplierDocumentLine(Base):
    __tablename__ = "supplier_document_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("supplier_documents.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty_micro: Mapped[int] = mapped_column(BigInteger)
    net_cents: Mapped[int] = mapped_column(BigInteger)      # line total net of discounts
    vat_rate_bp: Mapped[int] = mapped_column(Integer)
    vat_recoverable: Mapped[bool] = mapped_column(default=True)


class StockBalance(Base):
    __tablename__ = "stock_balances"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), primary_key=True)
    qty_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    value_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    last_unit_cost_micro: Mapped[int] = mapped_column(BigInteger, default=0)  # cents per base unit * 1e6


class StockMovement(Base):
    """Append-only. kind: receipt | supplier_return | sale_consumption |
    production_consumption | production_output | invoice_adjustment |
    cogs_adjustment (qty 0, value moved to COGS)."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    kind: Mapped[str] = mapped_column(String(24))
    qty_micro: Mapped[int] = mapped_column(BigInteger)       # signed
    value_cents: Mapped[int] = mapped_column(BigInteger)     # signed, stock value change
    cogs_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # cost recognized as COGS by this movement
    ref_type: Mapped[str] = mapped_column(String(24))
    ref_id: Mapped[str] = mapped_column(String(64))
    ref_line: Mapped[int] = mapped_column(Integer, default=0)
    negative_stock: Mapped[bool] = mapped_column(default=False)
    business_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime]

    __table_args__ = (UniqueConstraint("tenant_id", "kind", "ref_type", "ref_id", "ref_line", "item_id",
                                       name="uq_stock_movement_ref"),)


class Recipe(Base):
    """Versioned recipe of a menu offer; ingredients per ONE portion."""

    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    output_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"), nullable=True)  # batch recipes
    version: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[date] = mapped_column(Date)
    yield_micro: Mapped[int] = mapped_column(BigInteger, default=QTY_SCALE)  # output per batch of lines
    created_at: Mapped[datetime]

    lines: Mapped[list["RecipeLine"]] = relationship(lazy="selectin", order_by="RecipeLine.id")


class RecipeLine(Base):
    __tablename__ = "recipe_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty_micro: Mapped[int] = mapped_column(BigInteger)


class ProductionBatch(Base):
    __tablename__ = "production_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    output_item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    output_qty_micro: Mapped[int] = mapped_column(BigInteger)
    cost_cents: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]

    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_batch_idempotency"),)


# ------------------------------------------------------------ identity (RFC-0002)
# Secrets are never stored in clear: PINs/passwords as argon2id hashes, device
# and session tokens as SHA-256 of 256-bit random values, TOTP secrets
# encrypted (AES-GCM) with the server key.


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(default=True)
    pin_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    failed_pin_attempts: Mapped[int] = mapped_column(Integer, default=0)
    pin_locked_until: Mapped[datetime | None] = mapped_column(nullable=True)
    # Back-office (strong) login: only managers/owners get these.
    login: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    totp_secret_enc: Mapped[str | None] = mapped_column(String(200), nullable=True)
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    login_locked_until: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime]

    roles: Mapped[list["StaffRole"]] = relationship(lazy="selectin", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "display_name", name="uq_staff_name"),
        UniqueConstraint("tenant_id", "login", name="uq_staff_login"),
    )


class StaffRole(Base):
    """Role of a staff member, either tenant-wide (location NULL) or for one location."""

    __tablename__ = "staff_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    role: Mapped[str] = mapped_column(String(16))  # cashier|waiter|kitchen|manager|owner
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), nullable=True)

    __table_args__ = (UniqueConstraint("staff_id", "role", "location_id", name="uq_staff_role"),)


class Device(Base):
    """An enrolled till/tablet. Bound to one tenant + location (+ register)."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    register_id: Mapped[int | None] = mapped_column(ForeignKey("registers.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    enrolled_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime]
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    failed_pin_attempts: Mapped[int] = mapped_column(Integer, default=0)
    pin_locked_until: Mapped[datetime | None] = mapped_column(nullable=True)


class EnrollmentCode(Base):
    __tablename__ = "enrollment_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    register_id: Mapped[int | None] = mapped_column(ForeignKey("registers.id"), nullable=True)
    device_name: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class AuthSession(Base):
    """Server-side session: looked up on EVERY request, revocable at once."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff.id"), index=True)
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(8))  # till (PIN on device) | admin (password + TOTP)
    mfa: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Approval(Base):
    """Single-use manager step-up approval bound to ONE request (hash)."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    request_hash: Mapped[str] = mapped_column(String(64))
    approver_id: Mapped[str] = mapped_column(ForeignKey("staff.id"))
    approver_role: Mapped[str] = mapped_column(String(16))
    requested_by: Mapped[str] = mapped_column(ForeignKey("staff.id"))
    session_id: Mapped[str] = mapped_column(String(36))
    device_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AuthEvent(Base):
    """Append-only audit of authentication and authorization decisions."""

    __tablename__ = "auth_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    at: Mapped[datetime]
    event: Mapped[str] = mapped_column(String(48))
    outcome: Mapped[str] = mapped_column(String(16))  # ok|fail|locked|denied
    staff_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)


class DeviceJournalEntry(Base):
    """Append-only, hash-chained journal of money operations per stream
    (ADR-0012 §3). Today a stream is one register on the central server
    (``register:<id>``); on an offline edge node it is the device. The entry
    stores a hash of a canonical summary of the business record, so a later
    change to the sale/refund, a deleted entry or an edited hash is detected
    by ``journal.verify``. Sync (push/ack) is a later stage (O1b)."""

    __tablename__ = "device_journal"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)  # ULID
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    stream_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    entry_type: Mapped[str] = mapped_column(String(32))      # sale | refund
    entity_id: Mapped[str] = mapped_column(String(36))
    summary_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]

    __table_args__ = (UniqueConstraint("tenant_id", "stream_id", "seq", name="uq_journal_stream_seq"),)


class SyncOutbox(Base):
    """EDGE side: one row per journal entry to push to the central server,
    written in the same transaction as the sale (ADR-0012 §7). The payload is
    portable (register code, SKUs) because local ids differ from central ids.
    Rows are never deleted before the central server acknowledged them."""

    __tablename__ = "sync_outbox"

    entry_id: Mapped[str] = mapped_column(String(26), primary_key=True)   # = device_journal.id
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    entry_type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text)                              # canonical JSON
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")      # pending|acked|quarantined|rejected
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    server_result: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime]
    acked_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "device_id", "seq", name="uq_sync_outbox_seq"),)


class DeviceCursor(Base):
    """CENTRAL side: how far a device's journal has been ingested."""

    __tablename__ = "device_cursors"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)
    last_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime]


class DeviceInbox(Base):
    """CENTRAL side: every ingested device journal entry exactly once
    (unique device+seq). ``quarantined`` = received and acknowledged but NOT
    applied; a manager retries or dismisses it (never silently dropped)."""

    __tablename__ = "device_inbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    entry_id: Mapped[str] = mapped_column(String(26))
    entry_type: Mapped[str] = mapped_column(String(32))
    entry_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))      # accepted | quarantined | resolved | dismissed
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sale_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # created document: sale or refund (O3)
    received_at: Mapped[datetime]
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(300), nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "device_id", "seq", name="uq_device_inbox_seq"),
                      UniqueConstraint("tenant_id", "entry_id", name="uq_device_inbox_entry"))


class PosSettings(Base):
    """CENTRAL: per-tenant POS settings a manager edits in the settings screen.
    offline_limit_hours: how long a till may sell without contact with the
    central server (owner decision 2026-11-25: at most 72 h)."""

    __tablename__ = "pos_settings"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    offline_limit_hours: Mapped[int] = mapped_column(Integer, default=72)
    updated_at: Mapped[datetime]
    updated_by: Mapped[str] = mapped_column(String(64))


class EdgeSyncState(Base):
    """EDGE: the till's own record of its last successful contact with the
    central server and the limit the server gave it. ``max_seen_at`` is the
    latest local time the till has used; a clock set back behind it blocks
    selling (the limit cannot be dodged by changing the clock)."""

    __tablename__ = "edge_sync_state"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_contact_at: Mapped[datetime]
    max_seen_at: Mapped[datetime]
    offline_limit_hours: Mapped[int] = mapped_column(Integer)


APPEND_ONLY = (Sale, SaleLine, Refund, RefundLine, OutboxEvent, KitchenTicketLine, AuthEvent, StockMovement,
               BankTransaction, DeviceJournalEntry)


class AppendOnlyViolation(RuntimeError):
    pass


@event.listens_for(Session, "before_flush")
def _guard_append_only(session: Session, _ctx, _instances) -> None:
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, APPEND_ONLY) and (
            obj in session.deleted or session.is_modified(obj, include_collections=False)
        ):
            raise AppendOnlyViolation(
                f"{type(obj).__name__} is append-only; record a correction instead"
            )
