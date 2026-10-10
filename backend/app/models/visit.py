"""Visit log: visits to artisans and galleries, with what was said and agreed
and one optional photo (private, like the production photos). Only the people
holding the Visits permission (Sol and Hariel, plus the owner) read or write it.
``consent_to_publish`` is only recorded for now: nothing here is public yet."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

VISIT_KINDS = ("artesano", "galeria", "otro")


class Visit(Base):
    __tablename__ = "visit"
    __table_args__ = (
        CheckConstraint("kind IN ('artesano', 'galeria', 'otro')", name="ck_visit_kind"),
        CheckConstraint("length(btrim(summary)) >= 10", name="ck_visit_summary"),
        CheckConstraint("(photo_path IS NULL) = (photo_width IS NULL)", name="ck_visit_photo_consistent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visited_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    artisan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artisan.id", ondelete="SET NULL"), index=True)
    # The gallery or place visited (always for a gallery; optional for an artisan).
    place: Mapped[str | None] = mapped_column(Text)
    # Who from the team went (free text; the person who recorded it is always saved).
    attendees: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    agreements: Mapped[str | None] = mapped_column(Text)
    consent_to_publish: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    photo_path: Mapped[str | None] = mapped_column(Text)
    photo_width: Mapped[int | None] = mapped_column(Integer)
    photo_height: Mapped[int | None] = mapped_column(Integer)
    recorded_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                                 onupdate=func.now())
