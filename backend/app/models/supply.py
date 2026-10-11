"""Supplies the team consumes to make a certified piece: NFC chips, seals,
scratch cards, epoxy and so on (not stock of the pieces themselves: those are
unique works, ADR-011).

``supply`` is the catalogue (what, in which unit, and the minimum before a low
warning). ``supply_movement`` is an append-only ledger: purchases add, uses and
losses subtract, an adjustment fixes a miscount. The stock is the sum of the
movements, so it can always be explained and audited.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, func, literal_column
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SUPPLY_MOVEMENT_KINDS = ("purchase", "use", "loss", "adjustment")


class Supply(Base):
    __tablename__ = "supply"
    __table_args__ = (
        Index("uq_supply_name_lower", func.lower(literal_column("name")), unique=True),
        CheckConstraint("min_stock >= 0", name="ck_supply_min_stock"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # "pieza", "ml", "g", "rollo"... free text, shown next to the numbers.
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    min_stock: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                                 onupdate=func.now())


class SupplyMovement(Base):
    __tablename__ = "supply_movement"
    __table_args__ = (
        CheckConstraint("kind IN ('purchase', 'use', 'loss', 'adjustment')", name="ck_supply_movement_kind"),
        CheckConstraint(
            "(kind = 'purchase' AND delta > 0) OR (kind IN ('use', 'loss') AND delta < 0) "
            "OR (kind = 'adjustment' AND delta <> 0)", name="ck_supply_movement_delta"),
        CheckConstraint("unit_cost_cents IS NULL OR unit_cost_cents >= 0", name="ck_supply_movement_cost"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supply_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supply.id", ondelete="RESTRICT"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # Signed: positive adds to the stock, negative takes from it.
    delta: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    # Cost of one unit, in cents (purchases only).
    unit_cost_cents: Mapped[int | None] = mapped_column(Integer)
    # The piece a "use" went into, when it is known.
    piece_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("piece.id", ondelete="SET NULL"), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
