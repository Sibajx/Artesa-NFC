from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request
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
    CertificateClaimRequest,
    CertificateOriginal,
    CertificateResolveAuthentic,
    CertificateResolveRequest,
    CertificateResolveUnavailable,
    CertificateUnlockRefused,
    CertificateUnlockRequest,
    OriginalDesignPublic,
    OwnershipPublic,
    PinResetConfirm,
    PinResetRequest,
    PinResetResult,
)
from app.schemas.piece import piece_to_public
from app.services import designs, ownership
from app.services import mailer
from app.services.certificates import hash_certificate_token, is_syntactically_plausible_token

router = APIRouter(prefix="/certificates", tags=["certificates"])

# API_CONTRACT.md section 7 / SECURITY.md section 4: a single, reused
# instance for every non-authentic outcome, so the body can never
# accidentally drift between call sites in this module.
_UNAVAILABLE = CertificateResolveUnavailable(authenticity=AuthenticityUnavailable())


def _active_public_certificate(db: Session, token: str) -> Certificate | None:
    """The active certificate a token resolves to, with its piece and artisan
    both public; None for every other case (SECURITY.md section 4)."""
    # SECURITY.md section 4.1: a token that is not even syntactically
    # plausible (wrong length/alphabet) can skip the hash + DB roundtrip
    # entirely as a pure efficiency shortcut - it converges on the exact
    # same public body as every other unavailable case, never a different one.
    if not is_syntactically_plausible_token(token):
        return None
    token_hash = hash_certificate_token(token)
    # Single indexed equality lookup on token_hash, joined to piece/artisan
    # for the publication invariant (API_CONTRACT.md section 9, reusing the
    # exact predicate app/api/v1/pieces.py enforces) and eager-loaded so
    # nothing here re-queries piece/artisan (SECURITY.md/API_CONTRACT.md
    # require no N+1 for this hot path).
    return (
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


def _public_parts(db: Session, certificate: Certificate) -> dict:
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

    return {
        "authenticity": AuthenticityAuthentic(
            certificate_version=certificate.version,
            issued_at=certificate.issued_at,
            reported_stolen=piece.reported_stolen_at is not None,
        ),
        "piece": piece_to_public(piece, piece_media),
        "artisan": artisan_to_public(artisan, artisan_media, piece_summaries),
        "authenticity_metadata": AuthenticityMetadataPublic(notes=notes),
    }


@router.post("/resolve", response_model=CertificateResolveAuthentic | CertificateResolveUnavailable)
def resolve_certificate(
    body: CertificateResolveRequest, db: Session = Depends(get_db)
) -> CertificateResolveAuthentic | CertificateResolveUnavailable:
    certificate = _active_public_certificate(db, body.token)
    if certificate is None:
        return _UNAVAILABLE
    return CertificateResolveAuthentic(**_public_parts(db, certificate))


# --- ADR-030 phase 3: the original certificate behind the buyer's card -----------


def _client_ip(request: Request) -> str | None:
    # With Uvicorn's --proxy-headers behind cloudflared, client.host is the
    # visitor's address; anything that is not an IP is dropped.
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def _too_many(exc: ownership.TooManyAttempts) -> HTTPException:
    return HTTPException(status_code=429, detail={
        "code": "too_many_attempts",
        "message": "Too many attempts. Try again later.",
        "retry_after": exc.retry_after,
    })


def _original(db: Session, outcome: ownership.UnlockOutcome) -> CertificateOriginal:
    claim = outcome.claim
    db.refresh(outcome.card)
    design = designs.published(db, outcome.certificate.piece_id)
    return CertificateOriginal(
        design=OriginalDesignPublic(version=design.version, svg=designs.svg(db, design),
                                    approved_by_name=design.approved_by_name, approved_at=design.approved_at)
        if design else None,
        **_public_parts(db, outcome.certificate),
        ownership=OwnershipPublic(
            claimed=claim is not None,
            claimed_at=claim.claimed_at if claim else None,
            owner_email_masked=ownership.mask_email(claim.owner_email) if claim else None,
            card_issued_at=outcome.card.issued_at,
        ),
    )


@router.post("/unlock", response_model=CertificateOriginal | CertificateUnlockRefused)
def unlock_certificate(
    body: CertificateUnlockRequest, request: Request, db: Session = Depends(get_db)
) -> CertificateOriginal | CertificateUnlockRefused:
    """Token (from the chip) + card key (+ PIN once claimed) -> the original
    certificate. Every refusal is the same ``invalid`` (ADR-030)."""
    certificate = _active_public_certificate(db, body.token)
    try:
        outcome = ownership.unlock(db, certificate, body.key, body.pin, _client_ip(request))
    except ownership.TooManyAttempts as exc:
        raise _too_many(exc) from None
    if outcome.result != "unlocked":
        return CertificateUnlockRefused(result=outcome.result)
    return _original(db, outcome)


@router.post("/claim", response_model=CertificateOriginal | CertificateUnlockRefused)
def claim_piece(
    body: CertificateClaimRequest, request: Request, db: Session = Depends(get_db)
) -> CertificateOriginal | CertificateUnlockRefused:
    """First unlock by the owner: registers an email and a PIN; from then on
    the card alone is not enough."""
    email = body.email.strip().lower()
    if not ownership.EMAIL_RE.fullmatch(email):
        raise HTTPException(status_code=422, detail={"code": "invalid_email", "message": "Invalid email."})
    if not ownership.PIN_RE.fullmatch(body.pin):
        raise HTTPException(status_code=422, detail={"code": "invalid_pin", "message": "The PIN has 6 digits."})
    if ownership.is_weak_pin(body.pin):
        raise HTTPException(status_code=422, detail={"code": "weak_pin", "message": "Choose a less obvious PIN."})
    certificate = _active_public_certificate(db, body.token)
    try:
        outcome = ownership.unlock(db, certificate, body.key, body.pin, _client_ip(request), claim_email=email)
    except ownership.TooManyAttempts as exc:
        raise _too_many(exc) from None
    if outcome.result != "unlocked":
        return CertificateUnlockRefused(result=outcome.result)
    return _original(db, outcome)


@router.post("/pin-reset/request", response_model=PinResetResult)
def request_pin_reset(body: PinResetRequest, request: Request, db: Session = Depends(get_db)) -> PinResetResult:
    """The owner forgot the PIN: card key -> a code to the email registered in
    the claim. The answer is ``sent`` whatever the key, so it is no oracle."""
    certificate = _active_public_certificate(db, body.token)
    try:
        result = ownership.request_pin_reset(db, certificate, body.key, _client_ip(request))
    except ownership.TooManyAttempts as exc:
        raise _too_many(exc) from None
    except ownership.CodeRateLimited as exc:
        raise HTTPException(status_code=429, detail={
            "code": "too_many_attempts", "message": "Too many requests. Try again later.",
            "retry_after": exc.retry_after,
        }) from None
    except mailer.MailUnavailable:
        raise HTTPException(status_code=503, detail={"code": "mail_unavailable",
                                                      "message": "The email service is not available."}) from None
    return PinResetResult(result=result)


@router.post("/pin-reset/confirm", response_model=CertificateOriginal | CertificateUnlockRefused)
def confirm_pin_reset(
    body: PinResetConfirm, request: Request, db: Session = Depends(get_db)
) -> CertificateOriginal | CertificateUnlockRefused:
    """Card key + the emailed code + a new PIN: replaces the PIN and opens the
    original. Every refusal is the same ``invalid``."""
    if not ownership.PIN_RE.fullmatch(body.pin):
        raise HTTPException(status_code=422, detail={"code": "invalid_pin", "message": "The PIN has 6 digits."})
    if ownership.is_weak_pin(body.pin):
        raise HTTPException(status_code=422, detail={"code": "weak_pin", "message": "Choose a less obvious PIN."})
    certificate = _active_public_certificate(db, body.token)
    try:
        outcome = ownership.confirm_pin_reset(db, certificate, body.key, body.code.strip(), body.pin,
                                              _client_ip(request))
    except ownership.TooManyAttempts as exc:
        raise _too_many(exc) from None
    if outcome.result != "unlocked":
        return CertificateUnlockRefused(result=outcome.result)
    return _original(db, outcome)
