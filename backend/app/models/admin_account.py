"""P-026 G4: Gestión accounts managed from the owner's "Usuarios" page.

ADMIN_EMAILS in shared/.env stays as the fixed base (the owner and anyone
added by the operator); these rows add to it without a restart. A removed
account keeps its row (``active = false``) as history.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdminAccount(Base):
    __tablename__ = "admin_account"
    __table_args__ = (CheckConstraint("email = lower(email)", name="ck_admin_account_email_lower"),)

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    # One of "editor", "designer", "custodian" (each includes the ones before).
    role: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(Text)
    added_by: Mapped[str] = mapped_column(Text, nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    removed_by: Mapped[str | None] = mapped_column(Text)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
