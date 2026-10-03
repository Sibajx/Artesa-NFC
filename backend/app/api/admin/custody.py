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


# --- Phase 2: certify a piece and write its tag from Gestión (Web NFC) -----------
#
# These wrap the hardened provisioning services the CLI uses (ADR-026) so both
# paths share every rule: issue = register tag + assign + issue certificate in
# one transaction; program only after the browser wrote the tag AND read it
# back with the same UID; lock only after the browser locked it. The token
# leaves the server only in the issue/rotate response, to this custodian's
# browser (ADR-030 exception), with Cache-Control: no-store; it is never
# logged, stored or put in an audit event.

from pydantic import BaseModel, Field  # noqa: E402

from app.api.admin.writes import actor, require_write_guard  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services import provisioning as prov  # noqa: E402
from app.services.content import Actor  # noqa: E402
from app.services.lifecycle import LifecycleConflict, LifecycleError  # noqa: E402
from app.services.nfc_tags import (  # noqa: E402
    InvalidPhysicalUid,
    NfcTagUidAlreadyRegistered,
    normalize_physical_uid,
)


class UidBody(BaseModel):
    uid: str = Field(max_length=64)


class ProgramBody(BaseModel):
    tag_id: uuid.UUID
    uid: str = Field(max_length=64)


class RotateBody(BaseModel):
    reason: str = Field(max_length=32)
    # None = rewrite the same (unlocked) tag; a UID = a new tag.
    uid: str | None = Field(default=None, max_length=64)


class RevokeBody(BaseModel):
    reason: str = Field(max_length=32)


class CustodyTag(BaseModel):
    id: uuid.UUID
    status: str
    uid: str | None
    programmed_at: str | None
    locked_at: str | None


class CustodyState(BaseModel):
    piece_id: uuid.UUID
    public_code: str
    name: str
    artisan_name: str
    piece_published: bool
    artisan_published: bool
    certificate_active: bool
    certificate_issued_at: str | None
    revoked_certificates: int
    tags: list[CustodyTag]
    recommended_action: str
    issue_blockers: list[str]
    rotate_blockers: list[str]
    lock_blockers: list[str]
    revocation_reasons: list[str]


class CustodyIssued(BaseModel):
    """The only response that carries the certificate URL (ADR-030)."""
    url: str
    certificate_id: uuid.UUID
    tag_id: uuid.UUID
    uid: str | None
    needs_program: bool


writes_router = APIRouter(
    prefix="/api/admin/v1/custody",
    tags=["admin", "custody"],
    dependencies=[Depends(require_custodian), Depends(require_write_guard)],
)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _public_code(db: Session, piece_id: uuid.UUID) -> str:
    piece = db.get(Piece, piece_id)
    if piece is None or piece.trashed_at is not None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "The requested resource does not exist."})
    return piece.public_code


def _operator(who: Actor) -> str:
    # provisioning notes accept [A-Za-z0-9_.-]{1,32}: "web.<local part>".
    local = "".join(ch for ch in who.identity.email.split("@")[0] if ch.isalnum() or ch in "_.-")
    return f"web.{local}"[:32]


def _conflict(code: str, status: int = 409) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": code})


def _audit_custody(db: Session, who: Actor, piece_id: uuid.UUID, action: str, metadata: dict) -> None:
    db.add(AuditEvent(
        occurred_at=func.clock_timestamp(),
        actor_type=AuditActorType.admin_user,
        actor_email=who.identity.email,
        entity_type="piece",
        entity_id=piece_id,
        action=f"custody.{action}",
        result=AuditResult.success,
        ip_address=who.ip_address,
        event_metadata=metadata,
    ))


def _run(db: Session, call):
    try:
        return call()
    except prov.PreconditionFailed as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": exc.codes[0], "message": ", ".join(exc.codes),
                                                     "codes": list(exc.codes)}) from None
    except InvalidPhysicalUid as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"code": f"uid_{exc.reason}", "message": "Invalid NFC UID.",
                                                     "field": "uid"}) from None
    except NfcTagUidAlreadyRegistered:
        db.rollback()
        raise _conflict("uid_already_registered") from None
    except LifecycleConflict:
        db.rollback()
        raise _conflict("stale") from None
    except LifecycleError:
        db.rollback()
        raise _conflict("invalid_transition") from None


@router.get("/pieces/{piece_id}/state", response_model=CustodyState)
def custody_state(piece_id: uuid.UUID, db: Session = Depends(get_db)) -> CustodyState:
    state = prov.load_piece_state(db, _public_code(db, piece_id))
    return CustodyState(
        piece_id=state.piece_id,
        public_code=state.public_code,
        name=state.name,
        artisan_name=state.artisan_name,
        piece_published=state.piece_published,
        artisan_published=state.artisan_published,
        certificate_active=state.active_certificate is not None,
        certificate_issued_at=_iso(state.active_certificate.issued_at) if state.active_certificate else None,
        revoked_certificates=state.revoked_certificates,
        tags=[CustodyTag(id=t.id, status=t.status.value, uid=t.physical_uid,
                         programmed_at=_iso(t.programmed_at), locked_at=_iso(t.locked_at)) for t in state.tags],
        recommended_action=prov.recommended_action(state),
        issue_blockers=prov.issue_blockers(state),
        rotate_blockers=prov.rotate_blockers(state),
        lock_blockers=prov.lock_blockers(state),
        revocation_reasons=list(prov.REVOCATION_REASONS),
    )


def _issued(db: Session, code: str, raw_token: str, *, certificate_id, tag_id, uid, needs_program) -> CustodyIssued:
    # Same self-check as the CLI, after the commit: the token must resolve as
    # authentic to this very piece through the public resolve path.
    if not prov.verify_token_resolves(db, raw_token, code):
        raise _conflict("self_check_failed", 500)
    return CustodyIssued(url=prov.build_certificate_url(get_settings().app_env, raw_token),
                         certificate_id=certificate_id, tag_id=tag_id, uid=uid, needs_program=needs_program)


@writes_router.post("/pieces/{piece_id}/issue", response_model=CustodyIssued)
def custody_issue(piece_id: uuid.UUID, body: UidBody, who: Actor = Depends(actor),
                  db: Session = Depends(get_db)) -> CustodyIssued:
    code = _public_code(db, piece_id)
    result = _run(db, lambda: prov.execute_issue(db, code, body.uid, operator=_operator(who)))
    _audit_custody(db, who, piece_id, "issued", {"certificate_id": str(result.certificate_id),
                                                 "tag_id": str(result.tag_id), "uid": result.physical_uid})
    db.commit()
    return _issued(db, code, result.raw_token, certificate_id=result.certificate_id, tag_id=result.tag_id,
                   uid=result.physical_uid, needs_program=True)


@writes_router.post("/pieces/{piece_id}/rotate", response_model=CustodyIssued)
def custody_rotate(piece_id: uuid.UUID, body: RotateBody, who: Actor = Depends(actor),
                   db: Session = Depends(get_db)) -> CustodyIssued:
    code = _public_code(db, piece_id)
    result = _run(db, lambda: prov.execute_rotate(db, code, reason=body.reason, new_physical_uid=body.uid,
                                                  operator=_operator(who)))
    _audit_custody(db, who, piece_id, "rotated", {"certificate_id": str(result.certificate_id),
                                                  "revoked_certificate_id": str(result.revoked_certificate_id),
                                                  "tag_id": str(result.tag_id), "uid": result.physical_uid,
                                                  "reason": body.reason})
    db.commit()
    return _issued(db, code, result.raw_token, certificate_id=result.certificate_id, tag_id=result.tag_id,
                   uid=result.physical_uid, needs_program=result.needs_program)


@writes_router.post("/pieces/{piece_id}/program", response_model=CustodyState)
def custody_program(piece_id: uuid.UUID, body: ProgramBody, who: Actor = Depends(actor),
                    db: Session = Depends(get_db)) -> CustodyState:
    """After the browser wrote the URL and read the tag back: the UID it read
    must be the registered one, or a different chip was written."""
    code = _public_code(db, piece_id)
    tag = db.get(NfcTag, body.tag_id)
    if tag is None or tag.piece_id != piece_id:
        raise _conflict("tag_not_found", 404)
    read_back = _run(db, lambda: normalize_physical_uid(body.uid))
    if read_back != tag.physical_uid:
        raise _conflict("uid_mismatch")
    if tag.status == NfcTagStatus.programmed:
        result = _run(db, lambda: prov.execute_rewrite_note(db, code, tag.id, operator=_operator(who)))
    else:
        result = _run(db, lambda: prov.execute_program(db, code, tag.id, operator=_operator(who)))
    _audit_custody(db, who, piece_id, "programmed", {"tag_id": str(result.tag_id), "uid": result.physical_uid})
    db.commit()
    return custody_state(piece_id, db)


@writes_router.post("/pieces/{piece_id}/lock", response_model=CustodyState)
def custody_lock(piece_id: uuid.UUID, body: UidBody, who: Actor = Depends(actor),
                 db: Session = Depends(get_db)) -> CustodyState:
    code = _public_code(db, piece_id)
    read_back = _run(db, lambda: normalize_physical_uid(body.uid))
    state = prov.load_piece_state(db, code)
    if len(state.programmed_tags) != 1 or state.programmed_tags[0].physical_uid != read_back:
        raise _conflict("uid_mismatch")
    result = _run(db, lambda: prov.execute_lock(db, code, operator=_operator(who)))
    _audit_custody(db, who, piece_id, "locked", {"tag_id": str(result.tag_id), "uid": result.physical_uid})
    db.commit()
    return custody_state(piece_id, db)


@writes_router.post("/pieces/{piece_id}/revoke", response_model=CustodyState)
def custody_revoke(piece_id: uuid.UUID, body: RevokeBody, who: Actor = Depends(actor),
                   db: Session = Depends(get_db)) -> CustodyState:
    code = _public_code(db, piece_id)
    result = _run(db, lambda: prov.execute_revoke(db, code, reason=body.reason, operator=_operator(who)))
    _audit_custody(db, who, piece_id, "revoked", {"certificate_id": str(result.certificate_id),
                                                  "retired_tag_ids": [str(t) for t in result.retired_tag_ids],
                                                  "reason": body.reason})
    db.commit()
    return custody_state(piece_id, db)
