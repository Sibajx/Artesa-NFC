"""Fixed images of the public site that Gestión → Hero can replace (one row per slot).

A slot without a row keeps the provisional image built into the site. The
three files are named by content hash under ``publico/sitio/{slot}/``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SiteImage(Base):
    __tablename__ = "site_image"

    slot: Mapped[str] = mapped_column(Text, primary_key=True)
    avif: Mapped[str] = mapped_column(Text, nullable=False)
    webp: Mapped[str] = mapped_column(Text, nullable=False)
    jpg: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                                 onupdate=func.now())
