"""P-026 G1: the sale of a piece.

At most one active sale per piece; a cancelled sale stays as history.
Registering a sale is what marks the piece ``sold``; cancelling it puts the
piece back to ``available``. The buyer's name and contact are optional
personal data, only shown in Gestión and never audited.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SALE_CHANNELS = ("taller", "tienda", "en_linea", "feria", "otro")


class SaleStatus(str, enum.Enum):
    active = "active"
    cancelled = "cancelled"


class Sale(Base):
    __tablename__ = "sale"
    __table_args__ = (
        Index("uq_sale_one_active_per_piece", "piece_id", unique=True, postgresql_where=text("status = 'active'")),
        CheckConstraint("price_cents >= 0", name="ck_sale_price_cents"),
        CheckConstraint("channel IN ('taller', 'tienda', 'en_linea', 'feria', 'otro')", name="ck_sale_channel"),
        CheckConstraint("(status = 'cancelled') = (cancelled_at IS NOT NULL)", name="ck_sale_cancelled_at_matches_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    piece_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("piece.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[SaleStatus] = mapped_column(Enum(SaleStatus, name="sale_status"), nullable=False)
    sold_on: Mapped[date] = mapped_column(Date, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="MXN")
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    sold_by: Mapped[str] = mapped_column(Text, nullable=False)
    buyer_name: Mapped[str | None] = mapped_column(Text)
    buyer_contact: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_by: Mapped[str | None] = mapped_column(Text)
