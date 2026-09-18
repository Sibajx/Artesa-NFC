from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MediaType(str, enum.Enum):
    image = "image"
    video = "video"
    model_3d = "model_3d"
    sequence_360 = "sequence_360"


class MediaRole(str, enum.Enum):
    hero = "hero"
    gallery = "gallery"
    detail = "detail"
    process = "process"
    portrait = "portrait"
    document = "document"
    model_3d = "model_3d"
    sequence_360 = "sequence_360"


class MediaAssetStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class MediaAsset(Base):
    __tablename__ = "media_asset"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(artisan_id, piece_id) <= 1",
            name="ck_media_asset_single_owner",
        ),
        CheckConstraint(
            "media_type != 'image' OR alt_text IS NOT NULL",
            name="ck_media_asset_alt_text_required_for_image",
        ),
        Index(
            "ix_media_asset_owner_role_position",
            "artisan_id",
            "piece_id",
            "role",
            "position",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artisan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artisan.id", ondelete="SET NULL"), index=True
    )
    piece_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("piece.id", ondelete="SET NULL"), index=True
    )
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType, name="media_asset_type"), nullable=False)
    role: Mapped[MediaRole] = mapped_column(Enum(MediaRole, name="media_asset_role"), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    alt_text: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    format_metadata: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[MediaAssetStatus] = mapped_column(
        Enum(MediaAssetStatus, name="media_asset_status"),
        nullable=False,
        default=MediaAssetStatus.active,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    artisan: Mapped["Artisan | None"] = relationship(back_populates="media_assets")
    piece: Mapped["Piece | None"] = relationship(back_populates="media_assets")
