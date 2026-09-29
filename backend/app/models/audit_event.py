from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditActorType(str, enum.Enum):
    admin_user = "admin_user"
    system = "system"


class AuditResult(str, enum.Enum):
    success = "success"
    failure = "failure"


class AuditEvent(Base):
    """DATA_MODEL.md section 2.6. Append-only: the migration installs a
    trigger that rejects UPDATE and DELETE, so not even the application role
    can rewrite history. No updated_at on purpose.

    ``actor_email`` is the one addition to section 2.6: admin identity comes
    from Cloudflare Access (ADR-029), which gives an email, not a user id.
    ``entity_id`` is a logical reference (no foreign key): it points at
    different tables depending on ``entity_type``."""

    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_event_entity", "entity_type", "entity_id"),
        Index("ix_audit_event_occurred_at", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor_type: Mapped[AuditActorType] = mapped_column(
        Enum(AuditActorType, name="audit_actor_type"), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[AuditResult] = mapped_column(Enum(AuditResult, name="audit_result"), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET)
    # "metadata" is reserved on declarative classes; the column keeps its
    # documented name.
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)
