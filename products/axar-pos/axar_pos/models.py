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

    __table_args__ = (UniqueConstraint("tenant_id", "sku"),)


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

    lines: Mapped[list["SaleLine"]] = relationship(
        back_populates="sale", order_by="SaleLine.line_no", lazy="selectin"
    )
    register: Mapped[Register] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_sale_idempotency"),
        UniqueConstraint("tenant_id", "receipt_no", name="uq_sale_receipt_no"),
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
    """Transactional outbox; written in the same transaction as the sale."""

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
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|paid
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
        # One open bill per table, enforced by the database.
        Index("uq_one_open_order_per_table", "table_id", unique=True,
              sqlite_where=text("status = 'open'"), postgresql_where=text("status = 'open'")),
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


APPEND_ONLY = (Sale, SaleLine, Refund, RefundLine, OutboxEvent, KitchenTicketLine)


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
