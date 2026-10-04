"""ADR-030 phase 5: versions, review with the artisan, approval and
publication of a piece's designed original certificate.

See app/models/certificate_design.py for the workflow. Every change is
audited (``design.*``); the review token is never stored or audited.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import PRODUCTION_FRONTEND_ORIGIN
from app.models.artisan import Artisan
from app.models.audit_event import AuditActorType, AuditEvent, AuditResult
from app.models.certificate_design import CertificateDesign, DesignStatus
from app.models.piece import Piece
from app.services import certificate_render as renderer
from app.services.content import Actor, ContentConflict, ContentNotFound, StaleWrite

REVIEW_DAYS = 14
APPROVAL_MEDIA = ("enlace", "en persona", "whatsapp", "llamada", "otro")
OPEN = (DesignStatus.draft, DesignStatus.in_review, DesignStatus.approved)
_REVIEW_PATH = "/revision/#"
_REHEARSAL_REVIEW_BASE = "http://127.0.0.1:5500" + _REVIEW_PATH
# Mexico (Oaxaca) has no daylight saving time since 2022: a fixed UTC-6.
_LOCAL = timezone(timedelta(hours=-6), "CST")
TEXT_LIMITS = {"title": 60, "piece_name": 120, "artisan_name": 120, "quote": 240, "public_code": 32}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def review_url(app_env: str, token: str) -> str:
    base = PRODUCTION_FRONTEND_ORIGIN + _REVIEW_PATH if app_env == "production" else _REHEARSAL_REVIEW_BASE
    return base + token


def _now(db: Session) -> datetime:
    return db.execute(select(func.clock_timestamp())).scalar_one()


def _audit(db: Session, *, actor: Actor | None, design: CertificateDesign, action: str,
           metadata: dict | None = None, ip: str | None = None) -> None:
    db.add(AuditEvent(
        occurred_at=func.clock_timestamp(),
        actor_type=AuditActorType.admin_user if actor else AuditActorType.system,
        actor_email=actor.identity.email if actor else None,
        entity_type="piece",
        entity_id=design.piece_id,
        action=f"design.{action}",
        result=AuditResult.success,
        ip_address=actor.ip_address if actor else ip,
        event_metadata={"design_id": str(design.id), "version": design.version, **(metadata or {})},
    ))


def clean_params(raw: dict[str, Any]) -> dict[str, Any]:
    """Only known keys, bounded texts, valid colours and seed."""
    params: dict[str, Any] = {}
    template = raw.get("template")
    params["template"] = template if template in renderer.TEMPLATES else "clasico"
    variant = raw.get("variant")
    params["variant"] = variant if variant in renderer.VARIANTS else "claro"
    for key, limit in TEXT_LIMITS.items():
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            raise ContentConflict("invalid_design", f"{key} must be text.", key)
        value = (value or "").strip()
        if len(value) > limit:
            raise ContentConflict("invalid_design", f"{key} has at most {limit} characters.", key)
        params[key] = value
    palette = raw.get("palette") or []
    if not isinstance(palette, list) or not (3 <= len(palette) <= 5) or not all(
            isinstance(c, str) and renderer.HEX_RE.fullmatch(c.lower()) for c in palette):
        raise ContentConflict("invalid_design", "Use 3 to 5 colours like #a1b2c3.", "palette")
    params["palette"] = [c.lower() for c in palette]
    seed = raw.get("seed")
    params["seed"] = seed if isinstance(seed, int) and 0 <= seed < 2**31 else 1
    return params


def defaults(db: Session, piece: Piece) -> dict[str, Any]:
    artisan = db.get(Artisan, piece.artisan_id)
    palette = (piece.visual_theme or {}).get("palette") or ["#1d1915", "#c9761c", "#efe4cf"]
    return clean_params({
        "template": "clasico", "variant": "claro", "title": "Certificado original",
        "piece_name": piece.name, "artisan_name": (artisan.artistic_name or artisan.full_name) if artisan else "",
        "public_code": piece.public_code, "quote": "", "palette": palette, "seed": secrets.randbelow(2**31),
    })


def svg(design: CertificateDesign) -> str:
    # The date printed on the certificate is the artisan's local date.
    approved_on = design.approved_at.astimezone(_LOCAL).strftime("%d/%m/%Y") if design.approved_at else None
    watermark = None if design.status in (DesignStatus.approved, DesignStatus.published, DesignStatus.superseded) \
        else ("EN REVISIÓN" if design.status == DesignStatus.in_review else "BORRADOR")
    return renderer.render(design.params, version=design.version, approved_by=design.approved_by_name,
                           approved_on=approved_on, watermark=watermark)


def preview(params: dict[str, Any], version: int) -> str:
    return renderer.render(clean_params(params), version=version, watermark="BORRADOR")


def versions(db: Session, piece_id: uuid.UUID) -> list[CertificateDesign]:
    return list(db.execute(select(CertificateDesign).where(CertificateDesign.piece_id == piece_id)
                           .order_by(CertificateDesign.version.desc())).scalars())


def published(db: Session, piece_id: uuid.UUID) -> CertificateDesign | None:
    return db.execute(select(CertificateDesign).where(
        CertificateDesign.piece_id == piece_id, CertificateDesign.status == DesignStatus.published)).scalar_one_or_none()


def _locked(db: Session, design_id: uuid.UUID, expected: datetime | None = None) -> CertificateDesign:
    design = db.execute(select(CertificateDesign).where(CertificateDesign.id == design_id).with_for_update()
                        ).scalar_one_or_none()
    if design is None:
        raise ContentNotFound("not_found", "The requested resource does not exist.")
    if expected is not None and design.updated_at != expected:
        raise StaleWrite("stale", "This design changed since it was loaded. Reload it and try again.")
    return design


def _touch(design: CertificateDesign) -> None:
    design.updated_at = func.clock_timestamp()


def _commit(db: Session, design: CertificateDesign) -> CertificateDesign:
    db.commit()
    db.refresh(design)
    return design


# --- designer actions -------------------------------------------------------------------


def create(db: Session, actor: Actor, piece_id: uuid.UUID) -> CertificateDesign:
    """A new draft version: a copy of the latest version's params, or the
    defaults for a first design."""
    piece = db.execute(select(Piece).where(Piece.id == piece_id).with_for_update()).scalar_one_or_none()
    if piece is None or piece.trashed_at is not None:
        raise ContentNotFound("not_found", "The requested resource does not exist.")
    existing = versions(db, piece_id)
    if any(d.status in OPEN for d in existing):
        raise ContentConflict("open_design_exists", "This piece already has a design in progress.")
    params = dict(existing[0].params) if existing else defaults(db, piece)
    design = CertificateDesign(piece_id=piece_id, version=(existing[0].version + 1) if existing else 1,
                               status=DesignStatus.draft, template=params["template"], params=params,
                               created_by=actor.identity.email)
    db.add(design)
    db.flush()
    _audit(db, actor=actor, design=design, action="created")
    return _commit(db, design)


def update(db: Session, actor: Actor, design_id: uuid.UUID, expected: datetime, raw: dict[str, Any]) -> CertificateDesign:
    design = _locked(db, design_id, expected)
    if design.status not in (DesignStatus.draft, DesignStatus.in_review):
        raise ContentConflict("design_frozen", "An approved or published design cannot change. Create a new version.")
    params = clean_params({**design.params, **raw})
    if design.status == DesignStatus.in_review:
        # The artisan was reviewing something else: the link stops working.
        design.status = DesignStatus.draft
        design.review_token_hash = None
        design.review_expires_at = None
    design.params = params
    design.template = params["template"]
    design.change_request = None
    _touch(design)
    _audit(db, actor=actor, design=design, action="edited")
    return _commit(db, design)


def submit(db: Session, actor: Actor, design_id: uuid.UUID, expected: datetime) -> tuple[CertificateDesign, str]:
    """Draft -> in review; returns the raw review token (shown once)."""
    design = _locked(db, design_id, expected)
    if design.status not in (DesignStatus.draft, DesignStatus.in_review):
        raise ContentConflict("invalid_transition", "Only a draft can be sent for review.")
    token = secrets.token_urlsafe(32)
    design.status = DesignStatus.in_review
    design.review_token_hash = _hash(token)
    design.review_expires_at = _now(db) + timedelta(days=REVIEW_DAYS)
    design.submitted_at = _now(db)
    design.change_request = None
    _touch(design)
    _audit(db, actor=actor, design=design, action="submitted", metadata={"expires_days": REVIEW_DAYS})
    return _commit(db, design), token


def _approve(db: Session, design: CertificateDesign, *, name: str, medium: str, note: str | None,
             recorded_by: str) -> None:
    design.status = DesignStatus.approved
    design.approved_at = _now(db)
    design.approved_by_name = name
    design.approval_medium = medium
    design.approval_note = note
    design.approval_recorded_by = recorded_by
    design.review_token_hash = None
    design.review_expires_at = None
    _touch(design)


def record_approval(db: Session, actor: Actor, design_id: uuid.UUID, expected: datetime, *, name: str,
                    medium: str, note: str) -> CertificateDesign:
    """The artisan approved in person, by WhatsApp, ...; a designer records it."""
    design = _locked(db, design_id, expected)
    if design.status not in (DesignStatus.draft, DesignStatus.in_review):
        raise ContentConflict("invalid_transition", "Only a draft or a design under review can be approved.")
    if medium not in APPROVAL_MEDIA or not name.strip() or len(note.strip()) < 5:
        raise ContentConflict("invalid_approval", "Say who approved, how, and what you checked (5+ characters).")
    _approve(db, design, name=name.strip()[:120], medium=medium, note=note.strip()[:500],
             recorded_by=actor.identity.email)
    _audit(db, actor=actor, design=design, action="approved", metadata={"medium": medium, "by": design.approved_by_name})
    return _commit(db, design)


def publish(db: Session, actor: Actor, design_id: uuid.UUID, expected: datetime) -> CertificateDesign:
    design = _locked(db, design_id, expected)
    if design.status != DesignStatus.approved:
        raise ContentConflict("not_approved", "Only an approved design can be published.")
    previous = published(db, design.piece_id)
    if previous is not None:
        previous.status = DesignStatus.superseded
        _touch(previous)
        db.flush()
    design.status = DesignStatus.published
    design.published_at = _now(db)
    design.published_by = actor.identity.email
    _touch(design)
    _audit(db, actor=actor, design=design, action="published",
           metadata={"superseded_version": previous.version if previous else None})
    return _commit(db, design)


def discard(db: Session, actor: Actor, design_id: uuid.UUID, expected: datetime) -> None:
    """Deletes a version nobody has seen as the certificate yet: a draft, one
    under review, or one approved but never published (to start again). A
    published or superseded version is history and stays. An approval that
    is discarded remains in the audit trail."""
    design = _locked(db, design_id, expected)
    if design.status not in OPEN:
        raise ContentConflict("design_frozen", "A published version cannot be discarded.")
    _audit(db, actor=actor, design=design, action="discarded", metadata={
        "status": design.status.value, "approved_by": design.approved_by_name,
        "approval_medium": design.approval_medium})
    db.delete(design)
    db.commit()


# --- the artisan's review link ---------------------------------------------------------


def by_review_token(db: Session, token: str, *, for_update: bool = False) -> CertificateDesign | None:
    if not token or len(token) > 100:
        return None
    stmt = select(CertificateDesign).where(CertificateDesign.review_token_hash == _hash(token),
                                           CertificateDesign.status == DesignStatus.in_review)
    if for_update:
        stmt = stmt.with_for_update()
    design = db.execute(stmt).scalar_one_or_none()
    if design is None or design.review_expires_at is None or design.review_expires_at <= _now(db):
        return None
    return design


def review_decision(db: Session, token: str, *, approve: bool, comment: str | None, ip: str | None) -> bool:
    design = by_review_token(db, token, for_update=True)
    if design is None:
        db.rollback()
        return False
    comment = (comment or "").strip()[:500] or None
    if approve:
        _approve(db, design, name=design.params.get("artisan_name") or "El artesano", medium="enlace",
                 note=comment, recorded_by="enlace de revisión")
        _audit(db, actor=None, design=design, action="approved", metadata={"medium": "enlace"}, ip=ip)
    else:
        design.status = DesignStatus.draft
        design.review_token_hash = None
        design.review_expires_at = None
        design.change_request = comment or "El artesano pidió cambios."
        _touch(design)
        _audit(db, actor=None, design=design, action="changes_requested", ip=ip)
    db.commit()
    return True
