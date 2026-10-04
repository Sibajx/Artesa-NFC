"""P-026 G3: the artisan's authorization to publish, asked by WhatsApp.

Gestión creates a private link (/autorizacion/#token, 14 days) and opens a
WhatsApp chat with the artisan's validation number with the link in the
message. The artisan (or the trusted person who helps them) sees exactly
what will be published and taps "Sí, autorizo" or "No autorizo". What was
shown is kept as a snapshot. The team can also record an authorization given
in person. The token is stored only as SHA-256 and never audited.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import PRODUCTION_FRONTEND_ORIGIN, get_settings
from app.models.artisan import Artisan
from app.models.artisan_authorization import ArtisanAuthorization, AuthorizationStatus
from app.models.audit_event import AuditActorType, AuditEvent, AuditResult
from app.models.media_asset import MediaAsset, MediaAssetStatus, MediaRole
from app.services.content import Actor, ContentConflict, ContentNotFound

LINK_DAYS = 14
_PATH = "/autorizacion/#"
_REHEARSAL_BASE = "http://127.0.0.1:5500" + _PATH
_OPEN = (AuthorizationStatus.pending, AuthorizationStatus.authorized)


def normalize_whatsapp(raw: str | None) -> str | None:
    """Digits with country code. A 10-digit Mexican number gets 52."""
    if raw is None or not raw.strip():
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "52" + digits
    if not 11 <= len(digits) <= 15:
        raise ContentConflict("invalid_whatsapp", "Escribe el WhatsApp con lada, por ejemplo 951 123 4567.",
                              "validation_whatsapp")
    return digits


def link(token: str) -> str:
    base = PRODUCTION_FRONTEND_ORIGIN + _PATH if get_settings().app_env == "production" else _REHEARSAL_BASE
    return base + token


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now(db: Session) -> datetime:
    return db.execute(select(func.clock_timestamp())).scalar_one()


def _audit(db: Session, *, actor: Actor | None, artisan_id: uuid.UUID, action: str, metadata: dict,
           ip: str | None = None) -> None:
    db.add(AuditEvent(occurred_at=func.clock_timestamp(),
                      actor_type=AuditActorType.admin_user if actor else AuditActorType.system,
                      actor_email=actor.identity.email if actor else None, entity_type="artisan",
                      entity_id=artisan_id, action=f"artisan.{action}", result=AuditResult.success,
                      ip_address=actor.ip_address if actor else ip, event_metadata=metadata))


def snapshot(db: Session, artisan: Artisan) -> dict:
    portrait = db.execute(select(MediaAsset).where(
        MediaAsset.artisan_id == artisan.id, MediaAsset.status == MediaAssetStatus.active,
        MediaAsset.role == MediaRole.portrait).order_by(MediaAsset.position)).scalars().first()
    return {
        "full_name": artisan.full_name, "artistic_name": artisan.artistic_name,
        "place": ", ".join(p for p in (artisan.locality, artisan.municipality, artisan.state) if p),
        "biography": (artisan.biography or "")[:1200],
        "portrait": f"/media/{portrait.storage_path}" if portrait else None,
    }


def current(db: Session, artisan_id: uuid.UUID) -> ArtisanAuthorization | None:
    return db.execute(select(ArtisanAuthorization).where(
        ArtisanAuthorization.artisan_id == artisan_id, ArtisanAuthorization.status.in_(_OPEN))).scalar_one_or_none()


def is_authorized(db: Session, artisan_id: uuid.UUID) -> bool:
    row = current(db, artisan_id)
    return row is not None and row.status == AuthorizationStatus.authorized


def _artisan(db: Session, artisan_id: uuid.UUID) -> Artisan:
    artisan = db.execute(select(Artisan).where(Artisan.id == artisan_id).with_for_update()).scalar_one_or_none()
    if artisan is None or artisan.trashed_at is not None:
        raise ContentNotFound("not_found", "The requested resource does not exist.")
    return artisan


def request(db: Session, actor: Actor, artisan_id: uuid.UUID) -> tuple[ArtisanAuthorization, str]:
    """A new link (a pending request is replaced: its old link stops working)."""
    artisan = _artisan(db, artisan_id)
    row = current(db, artisan.id)
    if row is not None and row.status == AuthorizationStatus.authorized:
        raise ContentConflict("already_authorized", "El artesano ya autorizó la publicación.")
    token = secrets.token_urlsafe(32)
    expires = _now(db) + timedelta(days=LINK_DAYS)
    if row is None:
        row = ArtisanAuthorization(artisan_id=artisan.id, status=AuthorizationStatus.pending, medium="whatsapp",
                                   snapshot=snapshot(db, artisan), requested_by=actor.identity.email)
        db.add(row)
    row.snapshot = snapshot(db, artisan)
    row.token_hash, row.expires_at, row.requested_by = _hash(token), expires, actor.identity.email
    db.flush()
    _audit(db, actor=actor, artisan_id=artisan.id, action="authorization_requested",
           metadata={"authorization_id": str(row.id), "expires_days": LINK_DAYS})
    db.commit()
    return row, token


def by_token(db: Session, token: str, *, for_update: bool = False) -> ArtisanAuthorization | None:
    if not token or len(token) > 100:
        return None
    stmt = select(ArtisanAuthorization).where(ArtisanAuthorization.token_hash == _hash(token),
                                              ArtisanAuthorization.status == AuthorizationStatus.pending)
    if for_update:
        stmt = stmt.with_for_update()
    row = db.execute(stmt).scalar_one_or_none()
    if row is None or row.expires_at is None or row.expires_at <= _now(db):
        return None
    return row


def decide(db: Session, token: str, *, authorize: bool, comment: str | None, ip: str | None) -> bool:
    row = by_token(db, token, for_update=True)
    if row is None:
        db.rollback()
        return False
    row.status = AuthorizationStatus.authorized if authorize else AuthorizationStatus.declined
    row.decided_at, row.decided_ip = _now(db), ip
    row.note = (comment or "").strip()[:500] or None
    row.token_hash = None
    _audit(db, actor=None, artisan_id=row.artisan_id, ip=ip,
           action="authorized" if authorize else "authorization_declined",
           metadata={"authorization_id": str(row.id), "medium": "whatsapp"})
    db.commit()
    return True


def record_in_person(db: Session, actor: Actor, artisan_id: uuid.UUID, note: str) -> None:
    artisan = _artisan(db, artisan_id)
    if len(note.strip()) < 5:
        raise ContentConflict("invalid_authorization", "Escribe cómo autorizó (al menos 5 letras).", "note")
    row = current(db, artisan.id)
    if row is not None and row.status == AuthorizationStatus.authorized:
        raise ContentConflict("already_authorized", "El artesano ya autorizó la publicación.")
    if row is None:
        row = ArtisanAuthorization(artisan_id=artisan.id, status=AuthorizationStatus.pending, medium="en persona",
                                   snapshot=snapshot(db, artisan), requested_by=actor.identity.email)
        db.add(row)
    row.status, row.medium, row.snapshot = AuthorizationStatus.authorized, "en persona", snapshot(db, artisan)
    row.decided_at, row.note, row.token_hash, row.expires_at = _now(db), note.strip()[:500], None, None
    db.flush()
    _audit(db, actor=actor, artisan_id=artisan.id, action="authorized",
           metadata={"authorization_id": str(row.id), "medium": "en persona", "note": row.note})
    db.commit()


def revoke(db: Session, actor: Actor, artisan_id: uuid.UUID, note: str) -> None:
    artisan = _artisan(db, artisan_id)
    row = current(db, artisan.id)
    if row is None:
        raise ContentConflict("not_authorized", "No hay autorización que retirar.")
    if len(note.strip()) < 5:
        raise ContentConflict("invalid_authorization", "Escribe por qué se retira (al menos 5 letras).", "note")
    row.status, row.revoked_at, row.revoked_by = AuthorizationStatus.revoked, _now(db), actor.identity.email
    row.token_hash = None
    _audit(db, actor=actor, artisan_id=artisan.id, action="authorization_revoked",
           metadata={"authorization_id": str(row.id), "note": note.strip()[:500]})
    db.commit()
