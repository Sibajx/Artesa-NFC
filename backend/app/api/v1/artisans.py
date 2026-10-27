from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v1.common import (
    DEFAULT_MAX_PAGE_SIZE,
    decode_cursor,
    encode_cursor,
    fetch_media_assets,
    fetch_piece_summaries,
    not_found,
)
from app.models.artisan import Artisan
from app.models.enums import PublicationStatus
from app.models.piece import Piece
from app.schemas.artisan import ArtisanPublic, ArtisanSummary, artisan_to_public
from app.schemas.common import PagedListEnvelope, PagedListMeta

router = APIRouter(prefix="/artisans", tags=["artisans"])


@router.get("", response_model=PagedListEnvelope[ArtisanSummary])
def list_artisans(
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None, ge=1, le=DEFAULT_MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=300),
) -> PagedListEnvelope[ArtisanSummary]:
    published = Artisan.publication_status == PublicationStatus.published
    query = select(Artisan).where(published)
    total = db.execute(select(func.count()).select_from(Artisan).where(published)).scalar_one()
    if cursor is not None:
        query = query.where(Artisan.slug > decode_cursor(cursor))
    query = query.order_by(Artisan.slug.asc())
    if limit is not None:
        query = query.limit(limit + 1)
    artisans = list(db.execute(query).scalars().all())

    next_cursor = None
    if limit is not None and len(artisans) > limit:
        artisans = artisans[:limit]
        next_cursor = encode_cursor(artisans[-1].slug)

    data = [
        ArtisanSummary(slug=a.slug, full_name=a.full_name, artistic_name=a.artistic_name)
        for a in artisans
    ]
    return PagedListEnvelope(data=data, meta=PagedListMeta(total=total, next_cursor=next_cursor))


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
