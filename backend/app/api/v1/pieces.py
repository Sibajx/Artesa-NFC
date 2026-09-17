from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from app.api.deps import get_db
from app.api.v1.common import media_order_by, not_found
from app.models.artisan import Artisan
from app.models.enums import PublicationStatus
from app.models.media_asset import MediaAsset, MediaAssetStatus, MediaRole
from app.models.piece import AvailabilityStatus, Piece
from app.schemas.artisan import ArtisanPieceSummary, ArtisanSummary
from app.schemas.common import ListEnvelope, ListMeta
from app.schemas.media import media_asset_to_public
from app.schemas.piece import Dimensions, PiecePublic

router = APIRouter(prefix="/pieces", tags=["pieces"])


def _visible_pieces_query():
    # API_CONTRACT.md section 9 / DATA_MODEL.md section 9: a piece is public
    # iff both the piece itself and its artisan are published. Joining on
    # Artisan and filtering both columns enforces the invariant at the query
    # level, so a published piece under an unpublished artisan simply never
    # matches - no separate post-filter needed.
    return select(Piece).join(Artisan, Piece.artisan_id == Artisan.id).where(
        Piece.publication_status == PublicationStatus.published,
        Artisan.publication_status == PublicationStatus.published,
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

    cover_by_piece_id: dict = {}
    if pieces:
        cover_media_rows = (
            db.execute(
                select(MediaAsset)
                .where(
                    MediaAsset.piece_id.in_([p.id for p in pieces]),
                    MediaAsset.role == MediaRole.hero,
                    MediaAsset.status == MediaAssetStatus.active,
                )
                .order_by(*media_order_by())
            )
            .scalars()
            .all()
        )
        for media in cover_media_rows:
            cover_by_piece_id.setdefault(media.piece_id, media)

    data = [
        ArtisanPieceSummary(
            slug=piece.slug,
            name=piece.name,
            public_code=piece.public_code,
            availability_status=piece.availability_status.value,
            cover_media=(
                media_asset_to_public(cover_by_piece_id[piece.id])
                if piece.id in cover_by_piece_id
                else None
            ),
        )
        for piece in pieces
    ]
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

    media_rows = (
        db.execute(
            select(MediaAsset)
            .where(
                MediaAsset.piece_id == piece.id,
                MediaAsset.status == MediaAssetStatus.active,
            )
            .order_by(*media_order_by())
        )
        .scalars()
        .all()
    )

    dimensions = Dimensions(**piece.dimensions) if piece.dimensions else None

    return PiecePublic(
        slug=piece.slug,
        public_code=piece.public_code,
        name=piece.name,
        description=piece.description,
        history=piece.history,
        materials=piece.materials or [],
        technique=piece.technique,
        origin=piece.origin,
        creation_year=piece.creation_year,
        creation_date=piece.creation_date,
        dimensions=dimensions,
        visual_theme=piece.visual_theme,
        availability_status=piece.availability_status.value,
        artisan=ArtisanSummary(
            slug=piece.artisan.slug,
            full_name=piece.artisan.full_name,
            artistic_name=piece.artisan.artistic_name,
        ),
        media=[media_asset_to_public(media) for media in media_rows],
    )
