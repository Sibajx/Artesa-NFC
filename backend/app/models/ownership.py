"""ADR-030 phase 3: the buyer's scratch card and the claim on a piece.

Both belong to the piece, not to a certificate or a tag: replacing a chip
(rotate) must not invalidate the owner's card or claim.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OwnershipCardStatus(str, enum.Enum):
    active = "active"
    blocked = "blocked"
    replaced = "replaced"


class OwnershipCard(Base):
    """One printed card. Only the scrypt hash of its key is stored; the key is
    shown once, to the custodian who prints it. At most one current card
    (active or blocked) per piece; replaced cards stay as history."""

    __tablename__ = "ownership_card"
    __table_args__ = (
        Index(
            "uq_ownership_card_one_current_per_piece",
            "piece_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'blocked')"),
        ),
        CheckConstraint("failed_attempts >= 0", name="ck_ownership_card_failed_attempts"),
        CheckConstraint(
            "(status = 'replaced') = (replaced_at IS NOT NULL)",
            name="ck_ownership_card_replaced_at_matches_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    piece_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("piece.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[OwnershipCardStatus] = mapped_column(
        Enum(OwnershipCardStatus, name="ownership_card_status"), nullable=False
    )
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PieceClaimStatus(str, enum.Enum):
    active = "active"
    released = "released"


class PieceClaim(Base):
    """The owner registered an email and a PIN on first unlock; from then on
    the card alone is not enough. Only the PIN's scrypt hash is stored."""

    __tablename__ = "piece_claim"
    __table_args__ = (
        Index(
            "uq_piece_claim_one_active_per_piece",
            "piece_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        CheckConstraint(
            "(status = 'released') = (released_at IS NOT NULL)",
            name="ck_piece_claim_released_at_matches_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    piece_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("piece.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_email: Mapped[str] = mapped_column(Text, nullable=False)
    pin_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[PieceClaimStatus] = mapped_column(
        Enum(PieceClaimStatus, name="piece_claim_status"), nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
