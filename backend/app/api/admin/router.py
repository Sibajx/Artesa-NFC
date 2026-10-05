"""Gestión admin API, phase 1: read-only (ADR-029, API_CONTRACT.md section 14).

Every route depends on ``require_admin``: a verified Cloudflare Access token
for an allowlisted email. Without the admin configuration every route here
answers the same 404 body as an unknown route. Unlike the public API, drafts and
archived records are visible, and nothing is filtered by publication state.
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v1.common import media_order_by, not_found
from app.core.access import CUSTODIAN, AdminIdentity, require_admin
from app.models.artisan import Artisan
from app.models.audit_event import AuditEvent
from app.models.certificate import Certificate
from app.models.enums import PublicationStatus
from app.models.media_asset import MediaAsset
from app.models.nfc_tag import NfcTag
from app.models.piece import Piece
from app.schemas.admin import (
    AdminArtisanDetail,
    AdminAuthorization,
    AdminArtisanRef,
    AdminArtisanSummary,
    AdminAuditEvent,
    AdminCertificate,
    AdminMe,
    AdminMedia,
    AdminNfcTag,
    AdminPieceDetail,
    AdminSale,
    AdminPieceSummary,
)
from app.schemas.common import ListEnvelope, ListMeta
from app.schemas.media import media_asset_to_public
from app.services import media as media_service
from app.services import sales as sales_service
from app.services import trash as trash_service

# Mexico (Oaxaca): UTC-6, no daylight saving time since 2022.
_LOCAL = timezone(timedelta(hours=-6))

router = APIRouter(prefix="/api/admin/v1", tags=["admin"], dependencies=[Depends(require_admin)])

_MAX_QUERY_LENGTH = 100


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _admin_media(db: Session, *, piece_id: uuid.UUID | None = None, artisan_id: uuid.UUID | None = None) -> list[AdminMedia]:
    owner = MediaAsset.piece_id == piece_id if piece_id is not None else MediaAsset.artisan_id == artisan_id
    rows = db.execute(select(MediaAsset).where(owner).order_by(*media_order_by())).scalars().all()
    return [admin_media(db, m) for m in rows]


def admin_media(db: Session, asset: MediaAsset) -> AdminMedia:
    return AdminMedia(id=asset.id, status=asset.status.value, updated_at=asset.updated_at,
                      media=media_asset_to_public(asset), deletable=media_service.may_delete(db, asset))


def _piece_summaries(db: Session, pieces: list[Piece]) -> list[AdminPieceSummary]:
    slugs = {}
    if pieces:
        slugs = dict(db.execute(
            select(Artisan.id, Artisan.slug).where(Artisan.id.in_({p.artisan_id for p in pieces}))
        ).all())
    return [
        AdminPieceSummary(
            id=p.id,
            slug=p.slug,
            public_code=p.public_code,
            name=p.name,
            artisan_id=p.artisan_id,
            artisan_slug=slugs.get(p.artisan_id, ""),
            publication_status=p.publication_status.value,
            availability_status=p.availability_status.value,
            updated_at=p.updated_at,
            trashed_at=p.trashed_at,
        )
        for p in pieces
    ]


@router.get("/me", response_model=AdminMe)
def me(identity: AdminIdentity = Depends(require_admin)) -> AdminMe:
    return AdminMe(email=identity.email, roles=sorted(identity.roles))


@router.get("/artisans", response_model=ListEnvelope[AdminArtisanSummary])
def list_artisans(
    publication_status: PublicationStatus | None = None,
    q: str | None = Query(default=None, max_length=_MAX_QUERY_LENGTH),
    trashed: bool = False,
    db: Session = Depends(get_db),
) -> ListEnvelope[AdminArtisanSummary]:
    # The Papelera is its own list: ?trashed=true shows only trashed records,
    # every other list leaves them out.
    stmt = select(Artisan).where(Artisan.trashed_at.is_not(None) if trashed else Artisan.trashed_at.is_(None))
    if publication_status is not None:
        stmt = stmt.where(Artisan.publication_status == publication_status)
    if q and q.strip():
        pattern = _like(q.strip())
        stmt = stmt.where(or_(
            Artisan.full_name.ilike(pattern),
            Artisan.artistic_name.ilike(pattern),
            Artisan.slug.ilike(pattern),
        ))
    artisans = db.execute(stmt.order_by(Artisan.updated_at.desc(), Artisan.slug.asc())).scalars().all()

    counts = Counter()
    if artisans:
        counts.update(dict(db.execute(
            select(Piece.artisan_id, func.count())
            .where(Piece.artisan_id.in_([a.id for a in artisans]))
            .group_by(Piece.artisan_id)
        ).all()))

    data = [
        AdminArtisanSummary(
            id=a.id,
            slug=a.slug,
            full_name=a.full_name,
            artistic_name=a.artistic_name,
            publication_status=a.publication_status.value,
            piece_count=counts[a.id],
            updated_at=a.updated_at,
            trashed_at=a.trashed_at,
        )
        for a in artisans
    ]
    return ListEnvelope(data=data, meta=ListMeta(total=len(data)))


def _authorization(db: Session, artisan_id: uuid.UUID) -> AdminAuthorization | None:
    from app.services import authorizations

    return _admin_authorization(authorizations.current(db, artisan_id))


def _last_answer(db: Session, artisan_id: uuid.UUID) -> AdminAuthorization | None:
    from app.services import authorizations

    return _admin_authorization(authorizations.last_answer(db, artisan_id))


def _admin_authorization(row) -> AdminAuthorization | None:
    if row is None:
        return None
    return AdminAuthorization(id=row.id, status=row.status.value, medium=row.medium, requested_by=row.requested_by,
                              created_at=row.created_at, expires_at=row.expires_at, decided_at=row.decided_at,
                              note=row.note)


@router.get("/artisans/{artisan_id}", response_model=AdminArtisanDetail)
def get_artisan(artisan_id: uuid.UUID, db: Session = Depends(get_db),
                identity: AdminIdentity = Depends(require_admin)) -> AdminArtisanDetail:
    artisan = db.get(Artisan, artisan_id)
    if artisan is None:
        raise not_found()
    pieces = db.execute(
        select(Piece).where(Piece.artisan_id == artisan.id).order_by(Piece.updated_at.desc(), Piece.slug.asc())
    ).scalars().all()
    return AdminArtisanDetail(
        id=artisan.id,
        slug=artisan.slug,
        full_name=artisan.full_name,
        artistic_name=artisan.artistic_name,
        locality=artisan.locality,
        municipality=artisan.municipality,
        state=artisan.state,
        country=artisan.country,
        languages=artisan.languages,
        languages_public=artisan.languages_public,
        biography=artisan.biography,
        history=artisan.history,
        techniques=artisan.techniques,
        public_contact=artisan.public_contact,
        validation_whatsapp=artisan.validation_whatsapp,
        validation_contact_name=artisan.validation_contact_name,
        authorization=_authorization(db, artisan.id),
        last_answer=_last_answer(db, artisan.id),
        publication_status=artisan.publication_status.value,
        created_at=artisan.created_at,
        updated_at=artisan.updated_at,
        media=_admin_media(db, artisan_id=artisan.id),
        pieces=_piece_summaries(db, list(pieces)),
        trashed_at=artisan.trashed_at,
        purge_blocker=trash_service.purge_blocker(db, "artisan", artisan) if artisan.trashed_at else None,
    )


@router.get("/pieces", response_model=ListEnvelope[AdminPieceSummary])
def list_pieces(
    publication_status: PublicationStatus | None = None,
    artisan_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=_MAX_QUERY_LENGTH),
    trashed: bool = False,
    db: Session = Depends(get_db),
) -> ListEnvelope[AdminPieceSummary]:
    stmt = select(Piece).where(Piece.trashed_at.is_not(None) if trashed else Piece.trashed_at.is_(None))
    if publication_status is not None:
        stmt = stmt.where(Piece.publication_status == publication_status)
    if artisan_id is not None:
        stmt = stmt.where(Piece.artisan_id == artisan_id)
    if q and q.strip():
        pattern = _like(q.strip())
        stmt = stmt.where(or_(Piece.name.ilike(pattern), Piece.slug.ilike(pattern), Piece.public_code.ilike(pattern)))
    pieces = db.execute(stmt.order_by(Piece.updated_at.desc(), Piece.slug.asc())).scalars().all()
    data = _piece_summaries(db, list(pieces))
    return ListEnvelope(data=data, meta=ListMeta(total=len(data)))


@router.get("/pieces/{piece_id}", response_model=AdminPieceDetail)
def get_piece(piece_id: uuid.UUID, db: Session = Depends(get_db),
              identity: AdminIdentity = Depends(require_admin)) -> AdminPieceDetail:
    piece = db.get(Piece, piece_id)
    if piece is None:
        raise not_found()
    artisan = db.get(Artisan, piece.artisan_id)
    certificates = db.execute(
        select(Certificate).where(Certificate.piece_id == piece.id)
        .order_by(Certificate.created_at.desc(), Certificate.version.desc())
    ).scalars().all()
    tags = db.execute(
        select(NfcTag).where(NfcTag.piece_id == piece.id)
        .order_by(NfcTag.created_at.desc(), cast(NfcTag.status, String).asc())
    ).scalars().all()
    # ADR-030: certificates and NFC tags belong to the custody area.
    custody_visible = isinstance(identity, AdminIdentity) and identity.has(CUSTODIAN)
    if not custody_visible:
        certificates, tags = [], []
    return AdminPieceDetail(
        id=piece.id,
        slug=piece.slug,
        public_code=piece.public_code,
        name=piece.name,
        description=piece.description,
        history=piece.history,
        technique=piece.technique,
        materials=piece.materials,
        origin=piece.origin,
        creation_year=piece.creation_year,
        creation_date=piece.creation_date,
        dimensions=piece.dimensions,
        visual_theme=piece.visual_theme,
        price_cents=piece.price_cents,
        price_currency=piece.price_currency,
        availability_status=piece.availability_status.value,
        publication_status=piece.publication_status.value,
        sales=[AdminSale(**{f: getattr(s, f) for f in AdminSale.model_fields if f != "status"}, status=s.status.value)
               for s in sales_service.sales_of(db, piece.id)],
        publicly_visible=(
            piece.publication_status == PublicationStatus.published
            and artisan.publication_status == PublicationStatus.published
        ),
        created_at=piece.created_at,
        updated_at=piece.updated_at,
        artisan=AdminArtisanRef(
            id=artisan.id,
            slug=artisan.slug,
            full_name=artisan.full_name,
            publication_status=artisan.publication_status.value,
            trashed_at=artisan.trashed_at,
        ),
        trashed_at=piece.trashed_at,
        purge_blocker=trash_service.purge_blocker(db, "piece", piece) if piece.trashed_at else None,
        media=_admin_media(db, piece_id=piece.id),
        certificates=[
            AdminCertificate(
                id=c.id,
                status=c.status.value,
                version=c.version,
                issued_at=c.issued_at,
                revoked_at=c.revoked_at,
                revocation_reason=c.revocation_reason,
                created_at=c.created_at,
            )
            for c in certificates
        ],
        custody_visible=custody_visible,
        nfc_tags=[
            AdminNfcTag(
                id=t.id,
                status=t.status.value,
                chip_model=t.chip_model,
                programmed_at=t.programmed_at,
                locked_at=t.locked_at,
                created_at=t.created_at,
            )
            for t in tags
        ],
    )


@router.get("/summary")
def summary(db: Session = Depends(get_db), identity: AdminIdentity = Depends(require_admin)) -> dict:
    """P-026 G5/G8: what needs attention, shaped by the caller's roles."""
    from dataclasses import asdict

    from app.services import summary as summary_service

    data = summary_service.build(db, identity)
    return {k: asdict(v) if hasattr(v, "__dataclass_fields__") else v for k, v in data.items()}


@router.get("/audit-events", response_model=ListEnvelope[AdminAuditEvent])
def list_audit_events(
    entity_type: str | None = Query(default=None, max_length=50),
    entity_id: uuid.UUID | None = None,
    # P-026 G7: filters for the Auditoría page.
    action_prefix: str | None = Query(default=None, max_length=40, pattern=r"^[a-z_]+\.?[a-z_]*$"),
    actor_email: str | None = Query(default=None, max_length=254),
    since: date | None = None,
    until: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ListEnvelope[AdminAuditEvent]:
    stmt = select(AuditEvent)
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    if action_prefix:
        stmt = stmt.where(AuditEvent.action.startswith(action_prefix, autoescape=True))
    if actor_email:
        stmt = stmt.where(func.lower(AuditEvent.actor_email) == actor_email.strip().lower())
    # Dates are Mexico's local days (UTC-6, no DST since 2022).
    if since is not None:
        stmt = stmt.where(AuditEvent.occurred_at >= datetime.combine(since, time.min, _LOCAL))
    if until is not None:
        stmt = stmt.where(AuditEvent.occurred_at < datetime.combine(until + timedelta(days=1), time.min, _LOCAL))
    events = db.execute(stmt.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.asc()).limit(limit)).scalars().all()
    data = [
        AdminAuditEvent(
            id=e.id,
            occurred_at=e.occurred_at,
            actor_type=e.actor_type.value,
            actor_email=e.actor_email,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            action=e.action,
            result=e.result.value,
            metadata=e.event_metadata,
        )
        for e in events
    ]
    return ListEnvelope(data=data, meta=ListMeta(total=len(data)))
