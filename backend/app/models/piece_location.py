"""P-026 G12: where a piece physically is, as a log of moves.

The newest row is the piece's current location; older rows are its history.
Rows are never edited: a correction is a new move. They go away only with the
piece itself, when a never-public draft is purged from the trash.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PIECE_LOCATIONS = ("taller", "bodega", "tienda", "exhibicion", "transito", "entregada", "otro")


class PieceLocation(Base):
    __tablename__ = "piece_location"
    __table_args__ = (
        CheckConstraint(
            "location IN ('taller', 'bodega', 'tienda', 'exhibicion', 'transito', 'entregada', 'otro')",
            name="ck_piece_location_location",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    piece_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("piece.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location: Mapped[str] = mapped_column(Text, nullable=False)
    # Which shop, fair or museum, when the category is not enough.
    place: Mapped[str | None] = mapped_column(Text)
    moved_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())
