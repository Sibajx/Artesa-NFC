"""P-028: the home hero by season (campaigns managed from Gestión → Hero).

A campaign covers a range of days that repeats every year (month/day in
America/Mexico_City); the default campaign ("Hero normal") has no range. A
campaign shows on the site only when it is ``ready`` (its video was processed)
and ``published``, or when it is ``forced`` (for everyone, optionally until a
date). Files are named by content hash under ``publico/hero/{slug}/``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PROCESSING_STATES = ("none", "processing", "ready", "error")


class HeroCampaign(Base):
    __tablename__ = "hero_campaign"
    __table_args__ = (
        Index("uq_hero_campaign_one_default", "is_default", unique=True, postgresql_where=text("is_default")),
        Index("uq_hero_campaign_one_forced", "forced", unique=True, postgresql_where=text("forced")),
        CheckConstraint("processing_status IN ('none', 'processing', 'ready', 'error')",
                        name="ck_hero_campaign_processing_status"),
        CheckConstraint("start_month BETWEEN 1 AND 12 AND end_month BETWEEN 1 AND 12 "
                        "AND start_day BETWEEN 1 AND 31 AND end_day BETWEEN 1 AND 31",
                        name="ck_hero_campaign_days"),
        CheckConstraint("is_default = (start_month IS NULL)", name="ck_hero_campaign_default_has_no_range"),
        CheckConstraint("(start_month IS NULL) = (end_month IS NULL) AND (start_month IS NULL) = (start_day IS NULL) "
                        "AND (start_month IS NULL) = (end_day IS NULL)", name="ck_hero_campaign_range_complete"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    start_month: Mapped[int | None] = mapped_column(Integer)
    start_day: Mapped[int | None] = mapped_column(Integer)
    end_month: Mapped[int | None] = mapped_column(Integer)
    end_day: Mapped[int | None] = mapped_column(Integer)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    forced_until: Mapped[date | None] = mapped_column(Date)
    processing_status: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    processing_error: Mapped[str | None] = mapped_column(Text)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Paths relative to publico/ (e.g. "hero/dia-de-muertos/ab12cd34ef56.mp4").
    video_mp4: Mapped[str | None] = mapped_column(Text)
    video_webm: Mapped[str | None] = mapped_column(Text)
    poster: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                                 onupdate=func.now())
