from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, ForeignKey, Integer, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import PublicationStatus, publication_status_enum


class AvailabilityStatus(str, enum.Enum):
    available = "available"
    reserved = "reserved"
    exhibited = "exhibited"
    archived = "archived"
    # P-026 G1: only set by registering a sale (services/sales.py).
    sold = "sold"


class Piece(Base):
    __tablename__ = "piece"
    __table_args__ = (
        CheckConstraint("price_cents IS NULL OR price_cents >= 0", name="ck_piece_price_cents"),
    )

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
    # Gestión trash (2026-10): set while the record is in the Papelera. Only
    # draft or archived records go there; publishing or editing is refused
    # until it is restored, and only never-public records can be purged.
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ADR-030 phase 3: a custodian reported the piece stolen. Scanning its chip
    # still proves it is authentic, with a visible warning; unlocking the
    # original certificate is refused while it is set.
    reported_stolen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # P-026 G2: list price, Gestión only (never in the public API). Cents, so
    # sums never drift.
    price_cents: Mapped[int | None] = mapped_column(Integer)
    price_currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="MXN", default="MXN")

    artisan: Mapped["Artisan"] = relationship(back_populates="pieces")
    media_assets: Mapped[list["MediaAsset"]] = relationship(back_populates="piece")
    certificates: Mapped[list["Certificate"]] = relationship(back_populates="piece")
    # passive_deletes=True (unlike certificates/media_assets above): piece_id
    # here is nullable, so without this the ORM would silently UPDATE
    # nfc_tag.piece_id to NULL on parent delete instead of letting the DB's
    # ON DELETE RESTRICT (DATA_MODEL.md section 5) reject the delete.
    nfc_tags: Mapped[list["NfcTag"]] = relationship(back_populates="piece", passive_deletes=True)
