"""P-026 G3: the artisan's authorization to publish their name, portrait and
story. Asked through a WhatsApp link (or recorded in person by the team);
what was shown is kept as a snapshot. Publishing an artisan requires an
authorized row. The artisan can also ask for changes (nothing is unpublished)
or decline (the artisan and their pieces go back to draft at once). Revoking
from Gestión does not unpublish by itself (the team decides).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuthorizationStatus(str, enum.Enum):
    pending = "pending"
    authorized = "authorized"
    declined = "declined"
    changes_requested = "changes_requested"
    revoked = "revoked"


class ArtisanAuthorization(Base):
    __tablename__ = "artisan_authorization"
    __table_args__ = (
        Index("uq_artisan_authorization_one_open", "artisan_id", unique=True,
              postgresql_where=text("status IN ('pending', 'authorized')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artisan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artisan.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[AuthorizationStatus] = mapped_column(Enum(AuthorizationStatus, name="artisan_authorization_status"),
                                                        nullable=False)
    medium: Mapped[str] = mapped_column(Text, nullable=False)  # "whatsapp" | "en persona"
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    token_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_ip: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(Text)
