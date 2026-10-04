"""P-026 G1: registering and cancelling the sale of a piece.

Registering a sale is the only way a piece becomes ``sold``; cancelling it
puts the piece back to ``available``. Prices are integer cents. The buyer's
name and contact are optional personal data: kept on the sale row, never
copied into the audit trail.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.piece import AvailabilityStatus, Piece
from app.models.sale import SALE_CHANNELS, Sale, SaleStatus
from app.services.content import Actor, ContentConflict, _audit, _finish, _locked, _touch


def active_sale(db: Session, piece_id: uuid.UUID) -> Sale | None:
    return db.execute(select(Sale).where(Sale.piece_id == piece_id, Sale.status == SaleStatus.active)).scalar_one_or_none()


def sales_of(db: Session, piece_id: uuid.UUID) -> list[Sale]:
    return list(db.execute(select(Sale).where(Sale.piece_id == piece_id).order_by(Sale.created_at.desc())).scalars())


def register(db: Session, actor: Actor, piece_id: uuid.UUID, expected: datetime, data: dict[str, Any]) -> Piece:
    piece = _locked(db, Piece, piece_id, expected, "piece")
    if active_sale(db, piece.id) is not None:
        raise ContentConflict("already_sold", "This piece already has a registered sale. Cancel it first.")
    if data["channel"] not in SALE_CHANNELS:
        raise ContentConflict("invalid_sale", "Unknown sales channel.", "channel")
    if data["sold_on"] > date.today():
        raise ContentConflict("invalid_sale", "The sale date cannot be in the future.", "sold_on")
    sale = Sale(piece_id=piece.id, status=SaleStatus.active, recorded_by=actor.identity.email, **data)
    db.add(sale)
    before = piece.availability_status
    piece.availability_status = AvailabilityStatus.sold
    _touch(db, piece)
    db.flush()
    _audit(db, actor, "piece", piece.id, "piece.sold", {
        "sale_id": str(sale.id), "sold_on": data["sold_on"].isoformat(), "price_cents": data["price_cents"],
        "currency": data["currency"], "channel": data["channel"], "sold_by": data["sold_by"],
        "availability_before": before.value,
    })
    _finish(db, piece)
    return piece


def cancel(db: Session, actor: Actor, piece_id: uuid.UUID, expected: datetime, reason: str) -> Piece:
    piece = _locked(db, Piece, piece_id, expected, "piece")
    sale = active_sale(db, piece.id)
    if sale is None:
        raise ContentConflict("not_sold", "This piece has no registered sale.")
    reason = reason.strip()
    if len(reason) < 5:
        raise ContentConflict("invalid_sale", "Say why the sale is cancelled (5+ characters).", "reason")
    sale.status = SaleStatus.cancelled
    sale.cancelled_at = datetime.now().astimezone()
    sale.cancel_reason = reason[:500]
    sale.cancelled_by = actor.identity.email
    piece.availability_status = AvailabilityStatus.available
    _touch(db, piece)
    _audit(db, actor, "piece", piece.id, "piece.sale_cancelled", {"sale_id": str(sale.id), "reason": sale.cancel_reason})
    _finish(db, piece)
    return piece
