"""Persistence model.

Every business table carries ``tenant_id``. Sale, SaleLine and OutboxEvent are
append-only: an ORM guard refuses UPDATE/DELETE on them (ARCHITECTURE.md #4 —
corrections must be new auditable records, never edits).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


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
    price_cents: Mapped[int] = mapped_column(Integer)  # VAT-inclusive
    tax_category: Mapped[str] = mapped_column(String(32), default="standard")
    active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (UniqueConstraint("tenant_id", "sku"),)


class DrawerSession(Base):
    __tablename__ = "drawer_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|closed
    opened_by: Mapped[str] = mapped_column(String(64))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    opening_float_cents: Mapped[int] = mapped_column(Integer)
    closed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cash_sales_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    counted_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    variance_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # At most one open drawer per tenant, enforced by the database.
        Index(
            "uq_one_open_drawer_per_tenant",
            "tenant_id",
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
    drawer_session_id: Mapped[int] = mapped_column(ForeignKey("drawer_sessions.id"))
    cashier_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    business_date: Mapped[date] = mapped_column(Date, index=True)
    payment_method: Mapped[str] = mapped_column(String(16), default="cash")
    net_cents: Mapped[int] = mapped_column(Integer)
    vat_cents: Mapped[int] = mapped_column(Integer)
    total_cents: Mapped[int] = mapped_column(Integer)  # exact gross of all lines
    cash_rounding_cents: Mapped[int] = mapped_column(Integer)  # cash_due - total
    cash_due_cents: Mapped[int] = mapped_column(Integer)
    tendered_cents: Mapped[int] = mapped_column(Integer)
    change_cents: Mapped[int] = mapped_column(Integer)

    lines: Mapped[list["SaleLine"]] = relationship(
        back_populates="sale", order_by="SaleLine.line_no", lazy="selectin"
    )

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
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    gross_cents: Mapped[int] = mapped_column(Integer)
    tax_category: Mapped[str] = mapped_column(String(32))
    vat_rate_bp: Mapped[int] = mapped_column(Integer)
    vat_cents: Mapped[int] = mapped_column(Integer)
    net_cents: Mapped[int] = mapped_column(Integer)

    sale: Mapped[Sale] = relationship(back_populates="lines")


class OutboxEvent(Base):
    """Transactional outbox; written in the same transaction as the sale."""

    __tablename__ = "outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    type: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[str] = mapped_column(Text)  # canonical JSON
    payload_hash: Mapped[str] = mapped_column(String(64))


APPEND_ONLY = (Sale, SaleLine, OutboxEvent)


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
