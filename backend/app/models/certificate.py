from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CertificateStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    revoked = "revoked"


class Certificate(Base):
    __tablename__ = "certificate"
    __table_args__ = (
        # DATA_MODEL.md section 2.3: token_hash is null only while the
        # certificate is draft (not yet issued); required once active/revoked.
        CheckConstraint(
            "(status = 'draft' AND token_hash IS NULL) "
            "OR (status IN ('active', 'revoked') AND token_hash IS NOT NULL)",
            name="ck_certificate_token_hash_matches_status",
        ),
        # DATA_MODEL.md section 2.3: revoked_at is required exactly when
        # status = revoked, and must stay null otherwise.
        CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL) "
            "OR (status != 'revoked' AND revoked_at IS NULL)",
            name="ck_certificate_revoked_at_matches_status",
        ),
        # DATA_MODEL.md section 7: composite index to resolve a piece's full
        # certificate history (active + revoked + draft) without a table scan.
        Index("ix_certificate_piece_id_status", "piece_id", "status"),
        # DATA_MODEL.md section 4 restriction C' / section 6: at most one
        # active certificate per piece, historical rows preserved. Same
        # pattern already approved for nfc_tag.
        Index(
            "uq_certificate_one_active_per_piece",
            "piece_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    piece_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("piece.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CertificateStatus] = mapped_column(
        Enum(CertificateStatus, name="certificate_status"),
        nullable=False,
        default=CertificateStatus.draft,
        index=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    authenticity_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    piece: Mapped["Piece"] = relationship(back_populates="certificates")
