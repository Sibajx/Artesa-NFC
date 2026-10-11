"""Role profiles: named sets of permission checkboxes the owner edits in "Usuarios".

A profile is only a shortcut: an account never points at one. Its checkboxes
are what count, and the profile shown next to it is whichever one has exactly
the same set (or "A medida" when none does).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import ARRAY, CheckConstraint, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdminRoleProfile(Base):
    __tablename__ = "admin_role_profile"
    __table_args__ = (CheckConstraint("'view' = ANY(permissions)", name="ck_admin_role_profile_view"),)

    slug: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
