"""Settings of the price calculator (Gestión): one row, a validated JSON
config (app/services/pricing.py: PricingConfig). Kept as JSON so the accountant's
answer (tax model, rates) changes data, not the schema."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PricingSettings(Base):
    __tablename__ = "pricing_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_pricing_settings_single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[str] = mapped_column(Text, nullable=False)
