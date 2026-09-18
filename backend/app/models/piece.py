from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import PublicationStatus, publication_status_enum


class AvailabilityStatus(str, enum.Enum):
    available = "available"
    reserved = "reserved"
    exhibited = "exhibited"
    archived = "archived"


class Piece(Base):
    __tablename__ = "piece"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    public_code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    artisan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artisan.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    history: Mapped[str | None] = mapped_column(Text)
    technique: Mapped[str | None] = mapped_column(Text)
    materials: Mapped[list | None] = mapped_column(JSONB)
    origin: Mapped[str | None] = mapped_column(Text)
    creation_year: Mapped[int | None] = mapped_column(SmallInteger)
    creation_date: Mapped[date | None] = mapped_column(Date)
    dimensions: Mapped[dict | None] = mapped_column(JSONB)
    visual_theme: Mapped[dict | None] = mapped_column(JSONB)
    availability_status: Mapped[AvailabilityStatus] = mapped_column(
        Enum(AvailabilityStatus, name="piece_availability_status"),
        nullable=False,
        default=AvailabilityStatus.available,
    )
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

    artisan: Mapped["Artisan"] = relationship(back_populates="pieces")
    media_assets: Mapped[list["MediaAsset"]] = relationship(back_populates="piece")
    certificates: Mapped[list["Certificate"]] = relationship(back_populates="piece")
