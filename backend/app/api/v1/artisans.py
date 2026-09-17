from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v1.common import media_order_by, not_found
from app.models.artisan import Artisan
from app.models.enums import PublicationStatus
from app.models.media_asset import MediaAsset, MediaAssetStatus, MediaRole
from app.models.piece import Piece
from app.schemas.artisan import ArtisanPieceSummary, ArtisanPublic, ArtisanSummary, Location
from app.schemas.common import ListEnvelope, ListMeta
from app.schemas.media import media_asset_to_public

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

    media_rows = (
        db.execute(
            select(MediaAsset)
            .where(
                MediaAsset.artisan_id == artisan.id,
                MediaAsset.status == MediaAssetStatus.active,
            )
            .order_by(*media_order_by())
        )
        .scalars()
        .all()
    )

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

    piece_summaries = [
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

    return ArtisanPublic(
        slug=artisan.slug,
        full_name=artisan.full_name,
        artistic_name=artisan.artistic_name,
        biography=artisan.biography,
        history=artisan.history,
        location=Location(
            locality=artisan.locality,
            municipality=artisan.municipality,
            state=artisan.state,
            country=artisan.country,
        ),
        techniques=artisan.techniques or [],
        languages=(artisan.languages or []) if artisan.languages_public else [],
        public_contact=artisan.public_contact,
        media=[media_asset_to_public(media) for media in media_rows],
        pieces=piece_summaries,
    )
