"""A photo of a visit (private, like the production photos). A visit holds several,
up to ``MAX_PHOTOS``. Before this table a visit held one in ``visit.photo_*``; the
migration copied those over and the old columns are no longer read."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MAX_PHOTOS = 8


class VisitPhoto(Base):
    __tablename__ = "visit_photo"
    __table_args__ = (CheckConstraint("width > 0 AND height > 0", name="ck_visit_photo_size"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("visit.id", ondelete="CASCADE"), nullable=False, index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
