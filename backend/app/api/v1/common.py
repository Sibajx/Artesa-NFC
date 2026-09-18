from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import HTTPException
from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.models.artisan import Artisan
from app.models.enums import PublicationStatus
from app.models.media_asset import MediaAsset, MediaAssetStatus, MediaRole
from app.models.piece import Piece
from app.schemas.artisan import ArtisanPieceSummary
from app.schemas.media import MediaAssetPublic, media_asset_to_public

# API_CONTRACT.md section 10: unknown slug, unpublished record, and a record
# made ineligible by a related entity's publication state (e.g. a published
# piece under an unpublished artisan) must be byte-for-byte identical
# externally, so there is exactly one 404 body shared by every public
# detail endpoint.
NOT_FOUND_ERROR = {"code": "not_found", "message": "The requested resource does not exist."}


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND_ERROR)


def media_order_by():
    # Deterministic tie-break shared by every public endpoint that lists
    # media: position ASC, role ASC (as text, not native enum OID order),
    # storage_path ASC.
    return (MediaAsset.position.asc(), cast(MediaAsset.role, String).asc(), MediaAsset.storage_path.asc())


def piece_artisan_published_conditions():
    """Shared predicate for API_CONTRACT.md section 9's publication
    invariant: a piece is public iff both it and its artisan are published.
    Every query that must enforce this (piece listing/detail, certificate
    resolution) reuses this exact predicate so the rule cannot drift between
    call sites."""
    return (
        Piece.publication_status == PublicationStatus.published,
        Artisan.publication_status == PublicationStatus.published,
    )


def fetch_media_assets(
    db: Session, *, piece_id: uuid.UUID | None = None, artisan_id: uuid.UUID | None = None
) -> list[MediaAssetPublic]:
    """Active media for exactly one owner (piece xor artisan), in the shared
    display order. Reused by every public endpoint that embeds a
    MEDIA_ASSET array (API_CONTRACT.md section 6)."""
    if (piece_id is None) == (artisan_id is None):
        raise ValueError("exactly one of piece_id or artisan_id must be provided")

    owner_filter = (
        MediaAsset.piece_id == piece_id if piece_id is not None else MediaAsset.artisan_id == artisan_id
    )
    rows = (
        db.execute(
            select(MediaAsset)
            .where(owner_filter, MediaAsset.status == MediaAssetStatus.active)
            .order_by(*media_order_by())
        )
        .scalars()
        .all()
    )
    return [media_asset_to_public(media) for media in rows]


def fetch_piece_summaries(db: Session, pieces: Sequence[Piece]) -> list[ArtisanPieceSummary]:
    """Build the shared PIECE-summary-with-cover-media shape (embedded in
    both `artisan.pieces` and the `GET /pieces` list envelope,
    API_CONTRACT.md sections 4-5) for an already-fetched, already
    visibility-filtered list of pieces."""
    cover_by_piece_id: dict[uuid.UUID, MediaAsset] = {}
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

    return [
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
