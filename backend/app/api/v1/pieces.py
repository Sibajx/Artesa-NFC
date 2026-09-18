from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from app.api.deps import get_db
from app.api.v1.common import fetch_media_assets, fetch_piece_summaries, not_found, piece_artisan_published_conditions
from app.models.artisan import Artisan
from app.models.piece import AvailabilityStatus, Piece
from app.schemas.artisan import ArtisanPieceSummary
from app.schemas.common import ListEnvelope, ListMeta
from app.schemas.piece import PiecePublic, piece_to_public

router = APIRouter(prefix="/pieces", tags=["pieces"])


def _visible_pieces_query():
    # API_CONTRACT.md section 9 / DATA_MODEL.md section 9: a piece is public
    # iff both the piece itself and its artisan are published. Joining on
    # Artisan and filtering both columns enforces the invariant at the query
    # level, so a published piece under an unpublished artisan simply never
    # matches - no separate post-filter needed.
    return select(Piece).join(Artisan, Piece.artisan_id == Artisan.id).where(
        *piece_artisan_published_conditions()
    )


@router.get("", response_model=ListEnvelope[ArtisanPieceSummary])
def list_pieces(
    db: Session = Depends(get_db),
    artisan: str | None = Query(default=None),
    availability_status: AvailabilityStatus | None = Query(default=None),
) -> ListEnvelope[ArtisanPieceSummary]:
    query = _visible_pieces_query()
    if artisan is not None:
        query = query.where(Artisan.slug == artisan)
    if availability_status is not None:
        query = query.where(Piece.availability_status == availability_status)

    pieces = db.execute(query.order_by(Piece.slug.asc())).scalars().all()

    data = fetch_piece_summaries(db, pieces)
    return ListEnvelope(data=data, meta=ListMeta(total=len(data)))


@router.get("/{slug}", response_model=PiecePublic)
def get_piece(slug: str, db: Session = Depends(get_db)) -> PiecePublic:
    piece = db.execute(
        _visible_pieces_query()
        .where(Piece.slug == slug)
        .options(contains_eager(Piece.artisan))
    ).unique().scalar_one_or_none()

    if piece is None:
        raise not_found()

    media = fetch_media_assets(db, piece_id=piece.id)
    return piece_to_public(piece, media)
