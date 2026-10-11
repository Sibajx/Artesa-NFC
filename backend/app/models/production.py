"""Production follow-up of a piece: the steps from "received from the artisan"
to "delivered" (chip placed, chip programmed, packed, shipped), with who and
when, and optional private photos of each step.

``chip_programmed`` is never stored: it is read from the NFC tag (Certificación),
so nobody types the same fact twice. The photos are evidence for the team, not
public media: they live under ``privado/`` and never reach the public API.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PRODUCTION_STEPS = ("received", "chip_placed", "chip_programmed", "packed", "shipped", "delivered")
# Everything but chip_programmed, which comes from the NFC tag.
MANUAL_STEPS = tuple(s for s in PRODUCTION_STEPS if s != "chip_programmed")


class ProductionStep(Base):
    __tablename__ = "production_step"
    __table_args__ = (
        UniqueConstraint("piece_id", "step", name="uq_production_step_piece_step"),
        CheckConstraint("step IN ('received', 'chip_placed', 'packed', 'shipped', 'delivered')", name="ck_production_step_step"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    piece_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("piece.id", ondelete="CASCADE"), nullable=False, index=True)
    step: Mapped[str] = mapped_column(Text, nullable=False)
    done_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    done_by: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    # Shipping only.
    carrier: Mapped[str | None] = mapped_column(Text)
    tracking: Mapped[str | None] = mapped_column(Text)


class ProductionPhoto(Base):
    __tablename__ = "production_photo"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_step.id", ondelete="CASCADE"), nullable=False, index=True)
    # Relative to MEDIA_ROOT, always under privado/.
    path: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
