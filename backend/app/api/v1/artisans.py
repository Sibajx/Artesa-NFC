from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v1.common import fetch_media_assets, fetch_piece_summaries, not_found
from app.models.artisan import Artisan
from app.models.enums import PublicationStatus
from app.models.piece import Piece
from app.schemas.artisan import ArtisanPublic, ArtisanSummary, artisan_to_public
from app.schemas.common import ListEnvelope, ListMeta

router = APIRouter(prefix="/artisans", tags=["artisans"])


@router.get("", response_model=ListEnvelope[ArtisanSummary])
def list_artisans(db: Session = Depends(get_db)) -> ListEnvelope[ArtisanSummary]:
    artisans = (
        db.execute(
            select(Artisan)
            .where(Artisan.publication_status == PublicationStatus.published)
            .order_by(Artisan.slug.asc())
        )
        .scalars()
        .all()
    )

    data = [
        ArtisanSummary(slug=a.slug, full_name=a.full_name, artistic_name=a.artistic_name)
        for a in artisans
    ]
    return ListEnvelope(data=data, meta=ListMeta(total=len(data)))


@router.get("/{slug}", response_model=ArtisanPublic)
def get_artisan(slug: str, db: Session = Depends(get_db)) -> ArtisanPublic:
    artisan = db.execute(
        select(Artisan).where(
            Artisan.slug == slug,
            Artisan.publication_status == PublicationStatus.published,
        )
    ).scalar_one_or_none()

    if artisan is None:
        raise not_found()

    media = fetch_media_assets(db, artisan_id=artisan.id)

    pieces = (
        db.execute(
            select(Piece)
            .where(
                Piece.artisan_id == artisan.id,
                Piece.publication_status == PublicationStatus.published,
            )
            .order_by(Piece.slug.asc())
        )
        .scalars()
        .all()
    )
    piece_summaries = fetch_piece_summaries(db, pieces)

    return artisan_to_public(artisan, media, piece_summaries)
