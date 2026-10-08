"""ADR-030 phase 5b: artwork uploaded by the team for original certificates.

Content-addressed: the id is the SHA-256 of the stored (re-encoded) bytes, and
a row never changes nor goes away, so a frozen design that points at it
renders the same forever. Designs reference it from ``params["art"]``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, LargeBinary, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CertificateArt(Base):
    __tablename__ = "certificate_art"
    __table_args__ = (
        CheckConstraint("mime_type IN ('image/png', 'image/jpeg')", name="ck_certificate_art_mime_type"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_certificate_art_sha256"),
    )

    sha256: Mapped[str] = mapped_column(Text, primary_key=True)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
