"""Entries of the home's collection block (Gestión → Hero → Colecciones).

Each entry is a photo (4:5) with a title, a short text and a button that goes
to a page of the site. The home shows the active ones with a photo; with more
than one it rotates between them. With none, the block keeps the single
provisional image and text built into the site (and the ``collection-entry``
slot of ``site_image``).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CollectionEntry(Base):
    __tablename__ = "collection_entry"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) >= 3", name="ck_collection_entry_title"),
        CheckConstraint("position >= 0", name="ck_collection_entry_position"),
        CheckConstraint("(avif IS NULL) = (jpg IS NULL) AND (webp IS NULL) = (jpg IS NULL) "
                        "AND (width IS NULL) = (jpg IS NULL) AND (height IS NULL) = (jpg IS NULL)",
                        name="ck_collection_entry_image_consistent"),
        CheckConstraint("NOT active OR jpg IS NOT NULL", name="ck_collection_entry_active_has_image"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    position: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    cta_label: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    avif: Mapped[str | None] = mapped_column(Text)
    webp: Mapped[str | None] = mapped_column(Text)
    jpg: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                                 onupdate=func.now())
