"""P-026 G12: moving a piece between the workshop, storage, shops and shows.

Each move is a new ``piece_location`` row; the newest one is where the piece
is now. A move bumps the piece's version (If-Match) like any other write, and
is audited as ``piece.moved``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.piece import Piece
from app.models.piece_location import PIECE_LOCATIONS, PieceLocation
from app.services.content import Actor, ContentConflict, _audit, _finish, _locked, _touch


def history(db: Session, piece_id: uuid.UUID) -> list[PieceLocation]:
    """Newest first: the first row is the current location."""
    return list(db.execute(
        select(PieceLocation).where(PieceLocation.piece_id == piece_id)
        .order_by(PieceLocation.created_at.desc(), PieceLocation.id.desc())
    ).scalars())


def current_by_piece(db: Session, piece_ids: list[uuid.UUID]) -> dict[uuid.UUID, PieceLocation]:
    if not piece_ids:
        return {}
    rows = db.execute(
        select(PieceLocation).where(PieceLocation.piece_id.in_(piece_ids))
        .distinct(PieceLocation.piece_id)
        .order_by(PieceLocation.piece_id, PieceLocation.created_at.desc(), PieceLocation.id.desc())
    ).scalars()
    return {r.piece_id: r for r in rows}


def move(db: Session, actor: Actor, piece_id: uuid.UUID, expected: datetime, data: dict[str, Any]) -> Piece:
    piece = _locked(db, Piece, piece_id, expected, "piece")
    if data["location"] not in PIECE_LOCATIONS:
        raise ContentConflict("invalid_location", "Unknown location.", "location")
    if data["moved_on"] > date.today():
        raise ContentConflict("invalid_location", "The date of the move cannot be in the future.", "moved_on")
    before = history(db, piece.id)
    row = PieceLocation(piece_id=piece.id, recorded_by=actor.identity.email, **data)
    db.add(row)
    _touch(db, piece)
    db.flush()
    _audit(db, actor, "piece", piece.id, "piece.moved", {
        "location_id": str(row.id), "from": before[0].location if before else None,
        "to": row.location, "place": row.place, "moved_on": row.moved_on.isoformat(),
    })
    _finish(db, piece)
    return piece
