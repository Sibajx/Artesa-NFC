from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class NfcTagStatus(str, enum.Enum):
    available = "available"
    programmed = "programmed"
    locked = "locked"
    replaced = "replaced"
    retired = "retired"


class NfcTag(Base):
    __tablename__ = "nfc_tag"
    __table_args__ = (
        # DATA_MODEL.md section 2.4: a tag can't be programmed/locked without
        # being assigned to a piece.
        CheckConstraint(
            "status NOT IN ('programmed', 'locked') OR piece_id IS NOT NULL",
            name="ck_nfc_tag_assignment_requires_piece",
        ),
        # DATA_MODEL.md section 2.4 (amended, issue #70): locked_at is set
        # once the tag reaches locked, and preserved as historical metadata
        # if it later moves to replaced/retired — it must not be cleared
        # when a locked tag is superseded or decommissioned. available and
        # programmed tags must still have locked_at = NULL.
        CheckConstraint(
            "locked_at IS NULL OR status IN ('locked', 'replaced', 'retired')",
            name="ck_nfc_tag_locked_at_matches_status",
        ),
        # DATA_MODEL.md section 4 restriction B / section 6: at most one
        # active (programmed or locked) tag per piece, historical rows
        # preserved. Same pattern as uq_certificate_one_active_per_piece.
        Index(
            "uq_nfc_tag_one_active_per_piece",
            "piece_id",
            unique=True,
            postgresql_where=text("status IN ('programmed', 'locked')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    piece_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("piece.id", ondelete="RESTRICT"),
        index=True,
    )
    chip_model: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str | None] = mapped_column(Text)
    protocol: Mapped[str | None] = mapped_column(Text)
    # Opaque inventory metadata only (DATA_MODEL.md section 2.4, section 10;
    # SECURITY.md section 6): never used for authentication/authorization,
    # never treated as proof against cloning, never a substitute for the
    # certificate bearer token.
    physical_uid: Mapped[str | None] = mapped_column(Text, unique=True)
    programmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[NfcTagStatus] = mapped_column(
        Enum(NfcTagStatus, name="nfc_tag_status"),
        nullable=False,
        default=NfcTagStatus.available,
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    piece: Mapped["Piece | None"] = relationship(back_populates="nfc_tags")
