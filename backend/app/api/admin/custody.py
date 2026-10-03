"""Gestión custody area (ADR-030): certificates, card keys and NFC tags.

Only custodians (CUSTODIAN_EMAILS) may use /api/admin/v1/custody. The role is
checked here, server-side, on top of the separate Cloudflare Access
application that may guard this path. A refused attempt answers 403 and is
recorded in audit_event (result=failure), so probing the area leaves a trace.

Phase 1 is read-only: an overview of each piece's certification state.
Writes (token generation, tag writing, card keys) arrive in later phases.
"""
from __future__ import annotations

import ipaddress
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.access import CUSTODIAN, FORBIDDEN_ERROR, AdminIdentity, require_admin
from app.models.artisan import Artisan
from app.models.audit_event import AuditActorType, AuditEvent, AuditResult
from app.models.certificate import Certificate, CertificateStatus
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.models.piece import Piece
from app.schemas.admin import CustodyPiece
from app.schemas.common import ListEnvelope, ListMeta

_ACCESS_NAMESPACE = uuid.UUID("6a1f6f1e-6c38-4d0f-9b1e-3c0de5a0c0de")


def _ip(request: Request) -> str | None:
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def require_custodian(
    request: Request,
    identity: AdminIdentity = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminIdentity:
    if identity.has(CUSTODIAN):
        return identity
    db.add(AuditEvent(
        occurred_at=func.clock_timestamp(),
        actor_type=AuditActorType.admin_user,
        actor_email=identity.email,
        entity_type="custody",
        # No entity is involved: a stable id per account groups the attempts.
        entity_id=uuid.uuid5(_ACCESS_NAMESPACE, identity.email),
        action="custody.denied",
        result=AuditResult.failure,
        ip_address=_ip(request),
        event_metadata={"path": request.url.path},
    ))
    db.commit()
    raise HTTPException(status_code=403, detail=FORBIDDEN_ERROR)


router = APIRouter(
    prefix="/api/admin/v1/custody",
    tags=["admin", "custody"],
    dependencies=[Depends(require_custodian)],
)

_LIVE_TAGS = (NfcTagStatus.available, NfcTagStatus.programmed, NfcTagStatus.locked)


@router.get("/pieces", response_model=ListEnvelope[CustodyPiece])
def custody_pieces(db: Session = Depends(get_db)) -> ListEnvelope[CustodyPiece]:
    """Every piece (outside the trash) with its certification state."""
    pieces = db.execute(
        select(Piece, Artisan.full_name)
        .join(Artisan, Artisan.id == Piece.artisan_id)
        .where(Piece.trashed_at.is_(None))
        .order_by(Piece.updated_at.desc())
    ).all()
    ids = [p.id for p, _ in pieces]
    certs: dict[uuid.UUID, Certificate] = {}
    tags: dict[uuid.UUID, NfcTag] = {}
    if ids:
        for cert in db.execute(
            select(Certificate).where(Certificate.piece_id.in_(ids)).order_by(Certificate.version.asc())
        ).scalars():
            certs[cert.piece_id] = cert  # highest version wins
        for tag in db.execute(
            select(NfcTag).where(NfcTag.piece_id.in_(ids), NfcTag.status.in_(_LIVE_TAGS))
            .order_by(NfcTag.created_at.asc())
        ).scalars():
            tags[tag.piece_id] = tag
    data = [
        CustodyPiece(
            id=piece.id,
            slug=piece.slug,
            public_code=piece.public_code,
            name=piece.name,
            artisan_name=artisan_name,
            publication_status=piece.publication_status.value,
            certificate_status=certs[piece.id].status.value if piece.id in certs else None,
            certificate_version=certs[piece.id].version if piece.id in certs else None,
            tag_status=tags[piece.id].status.value if piece.id in tags else None,
            tag_chip=tags[piece.id].chip_model if piece.id in tags else None,
            ready_to_certify=(
                piece.publication_status.value == "published"
                and (piece.id not in certs or certs[piece.id].status != CertificateStatus.active)
            ),
        )
        for piece, artisan_name in pieces
    ]
    return ListEnvelope(data=data, meta=ListMeta(total=len(data)))
