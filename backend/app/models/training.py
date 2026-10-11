"""Capacitaciones: training manuals per craft, in a written version and a version
with drawings, that the owner reviews inside Gestión (draft -> in review ->
approved / changes requested). Drawings are private images, like the production
photos. Only the people holding the Training permission read or write them; only
the owner reviews and deletes."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TRAINING_STATUSES = ("draft", "in_review", "changes_requested", "approved")


class TrainingManual(Base):
    __tablename__ = "training_manual"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'in_review', 'changes_requested', 'approved')", name="ck_training_manual_status"),
        CheckConstraint("length(btrim(title)) >= 3", name="ck_training_manual_title"),
        CheckConstraint("length(btrim(craft)) >= 2", name="ck_training_manual_craft"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    craft: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    written: Mapped[str | None] = mapped_column(Text)
    visual: Mapped[str | None] = mapped_column(Text)
    next_steps: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft", server_default="draft")
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                                 onupdate=func.now())


class TrainingImage(Base):
    __tablename__ = "training_image"
    __table_args__ = (CheckConstraint("width > 0 AND height > 0", name="ck_training_image_size"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manual_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("training_manual.id", ondelete="CASCADE"), nullable=False, index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
