from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from app.api.deps import get_db
from app.api.v1.common import fetch_media_assets, fetch_piece_summaries, piece_artisan_published_conditions
from app.models.artisan import Artisan
from app.models.certificate import Certificate, CertificateStatus
from app.models.enums import PublicationStatus
from app.models.piece import Piece
from app.schemas.artisan import artisan_to_public
from app.schemas.certificate import (
    AuthenticityAuthentic,
    AuthenticityMetadataPublic,
    AuthenticityUnavailable,
    CertificateResolveAuthentic,
    CertificateResolveRequest,
    CertificateResolveUnavailable,
)
from app.schemas.piece import piece_to_public
from app.services.certificates import hash_certificate_token, is_syntactically_plausible_token

router = APIRouter(prefix="/certificates", tags=["certificates"])

# API_CONTRACT.md section 7 / SECURITY.md section 4: a single, reused
# instance for every non-authentic outcome, so the body can never
# accidentally drift between call sites in this module.
_UNAVAILABLE = CertificateResolveUnavailable(authenticity=AuthenticityUnavailable())


@router.post("/resolve", response_model=CertificateResolveAuthentic | CertificateResolveUnavailable)
def resolve_certificate(
    body: CertificateResolveRequest, db: Session = Depends(get_db)
) -> CertificateResolveAuthentic | CertificateResolveUnavailable:
    # SECURITY.md section 4.1: a token that is not even syntactically
    # plausible (wrong length/alphabet) can skip the hash + DB roundtrip
    # entirely as a pure efficiency shortcut - it converges on the exact
    # same public body as every other unavailable case below, never a
    # different one.
    if not is_syntactically_plausible_token(body.token):
        return _UNAVAILABLE

    token_hash = hash_certificate_token(body.token)

    # Single indexed equality lookup on token_hash, joined to piece/artisan
    # for the publication invariant (API_CONTRACT.md section 9, reusing the
    # exact predicate app/api/v1/pieces.py enforces) and eager-loaded so
    # nothing here re-queries piece/artisan (SECURITY.md/API_CONTRACT.md
    # require no N+1 for this hot path).
    certificate = (
        db.execute(
            select(Certificate)
            .join(Piece, Certificate.piece_id == Piece.id)
            .join(Artisan, Piece.artisan_id == Artisan.id)
            .where(
                Certificate.token_hash == token_hash,
                Certificate.status == CertificateStatus.active,
                *piece_artisan_published_conditions(),
            )
            .options(contains_eager(Certificate.piece).contains_eager(Piece.artisan))
        )
        .unique()
        .scalar_one_or_none()
    )

    if certificate is None:
        return _UNAVAILABLE

    piece = certificate.piece
    artisan = piece.artisan

    piece_media = fetch_media_assets(db, piece_id=piece.id)
    artisan_media = fetch_media_assets(db, artisan_id=artisan.id)

    artisan_pieces = (
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
    piece_summaries = fetch_piece_summaries(db, artisan_pieces)

    # API_CONTRACT.md section 7: authenticity_metadata is an explicit
    # allowlist projection (only `notes`), never the raw JSONB column.
    metadata = certificate.authenticity_metadata or {}
    notes = metadata.get("notes")
    if not isinstance(notes, str):
        notes = None

    return CertificateResolveAuthentic(
        authenticity=AuthenticityAuthentic(
            certificate_version=certificate.version,
            issued_at=certificate.issued_at,
        ),
        piece=piece_to_public(piece, piece_media),
        artisan=artisan_to_public(artisan, artisan_media, piece_summaries),
        authenticity_metadata=AuthenticityMetadataPublic(notes=notes),
    )
