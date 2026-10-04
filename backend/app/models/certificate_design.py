"""ADR-030 phase 5: the designed original certificate of a piece.

One row per version. Workflow:

    draft -> in_review -> approved -> published -> superseded
       ^         |
       +---------+   (editing a design under review sends it back to draft)

- Only a draft can be edited. Approved and published versions are frozen; a
  redesign is the next version.
- At most one open version (draft, in_review or approved) and at most one
  published version per piece.
- The artisan's review link is a random token stored only as its SHA-256
  hash, with an expiry.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DesignStatus(str, enum.Enum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    published = "published"
    superseded = "superseded"


class CertificateDesign(Base):
    __tablename__ = "certificate_design"
    __table_args__ = (
        UniqueConstraint("piece_id", "version", name="uq_certificate_design_piece_version"),
        Index(
            "uq_certificate_design_one_open_per_piece",
            "piece_id",
            unique=True,
            postgresql_where=text("status IN ('draft', 'in_review', 'approved')"),
        ),
        Index(
            "uq_certificate_design_one_published_per_piece",
            "piece_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        CheckConstraint("version >= 1", name="ck_certificate_design_version"),
        CheckConstraint(
            "(status IN ('approved', 'published', 'superseded')) = (approved_at IS NOT NULL)",
            name="ck_certificate_design_approved_at_matches_status",
        ),
        CheckConstraint(
            "(status IN ('published', 'superseded')) = (published_at IS NOT NULL)",
            name="ck_certificate_design_published_at_matches_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    piece_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("piece.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DesignStatus] = mapped_column(Enum(DesignStatus, name="certificate_design_status"), nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Review with the artisan.
    review_token_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    review_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "Pedir cambios" from the artisan's link: the last comment, until edited.
    change_request: Mapped[str | None] = mapped_column(Text)
    # Approval: who approved (the artisan), how, evidence, and who recorded it.
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_name: Mapped[str | None] = mapped_column(Text)
    approval_medium: Mapped[str | None] = mapped_column(Text)
    approval_note: Mapped[str | None] = mapped_column(Text)
    approval_recorded_by: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str | None] = mapped_column(Text)
