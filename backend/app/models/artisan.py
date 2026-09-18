from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import PublicationStatus, publication_status_enum


class Artisan(Base):
    __tablename__ = "artisan"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    artistic_name: Mapped[str | None] = mapped_column(Text)
    locality: Mapped[str | None] = mapped_column(Text)
    municipality: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text, default="Oaxaca")
    country: Mapped[str | None] = mapped_column(Text, default="México")
    languages: Mapped[list | None] = mapped_column(JSONB)
    languages_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    biography: Mapped[str | None] = mapped_column(Text)
    history: Mapped[str | None] = mapped_column(Text)
    techniques: Mapped[list | None] = mapped_column(JSONB)
    public_contact: Mapped[dict | None] = mapped_column(JSONB)
    publication_status: Mapped[PublicationStatus] = mapped_column(
        publication_status_enum,
        nullable=False,
        default=PublicationStatus.draft,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    pieces: Mapped[list["Piece"]] = relationship(back_populates="artisan")
    media_assets: Mapped[list["MediaAsset"]] = relationship(back_populates="artisan")
