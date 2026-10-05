"""P-026 G3: the artisan's authorization to publish, asked by WhatsApp.

Gestión creates a private link (/autorizacion/#token, 14 days) and opens a
WhatsApp chat with the artisan's validation number with the link in the
message. The artisan (or the trusted person who helps them) sees everything
that is published about them (name, portrait, biography, history,
techniques, public contact and their published pieces) and answers:

- "Sí, autorizo": authorized.
- "Quiero cambios", with a comment: the request closes and nothing is
  unpublished; the team corrects the record and sends a new link.
- "No autorizo": the artisan and their published pieces go back to draft at
  once, because the page promises that nothing is published without consent.

What was shown is kept as a snapshot. The team can also record an
authorization given in person, and later ask for the artisan's confirmation
by WhatsApp: the authorization stays valid while the link is open. The token
is stored only as SHA-256 and never audited.
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
from app.models.enums import PublicationStatus
from app.models.media_asset import MediaAsset, MediaAssetStatus, MediaRole
from app.models.piece import Piece
from app.services.content import Actor, ContentConflict, ContentNotFound

LINK_DAYS = 14
_PATH = "/autorizacion/#"
_REHEARSAL_BASE = "http://127.0.0.1:5500" + _PATH
_OPEN = (AuthorizationStatus.pending, AuthorizationStatus.authorized)
# The artisan's answers through the link; "changes" needs a comment.
DECISIONS = ("authorize", "changes", "decline")
_MIN_COMMENT = 3


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
           ip: str | None = None, entity_type: str = "artisan") -> None:
    db.add(AuditEvent(occurred_at=func.clock_timestamp(),
                      actor_type=AuditActorType.admin_user if actor else AuditActorType.system,
                      actor_email=actor.identity.email if actor else None, entity_type=entity_type,
                      entity_id=artisan_id, action=f"{entity_type}.{action}", result=AuditResult.success,
                      ip_address=actor.ip_address if actor else ip, event_metadata=metadata))


def _published_pieces(db: Session, artisan_id: uuid.UUID, *, for_update: bool = False) -> list[Piece]:
    stmt = select(Piece).where(Piece.artisan_id == artisan_id, Piece.trashed_at.is_(None),
                               Piece.publication_status == PublicationStatus.published).order_by(Piece.slug)
    return list(db.execute(stmt.with_for_update() if for_update else stmt).scalars())


def snapshot(db: Session, artisan: Artisan) -> dict:
    """Everything the site publishes about the artisan, in full: the artisan
    must not authorize something they did not see."""
    from app.api.v1.common import fetch_piece_summaries  # api -> services elsewhere

    portrait = db.execute(select(MediaAsset).where(
        MediaAsset.artisan_id == artisan.id, MediaAsset.status == MediaAssetStatus.active,
        MediaAsset.role == MediaRole.portrait).order_by(MediaAsset.position)).scalars().first()
    pieces = fetch_piece_summaries(db, _published_pieces(db, artisan.id))
    return {
        "full_name": artisan.full_name, "artistic_name": artisan.artistic_name,
        "place": ", ".join(p for p in (artisan.locality, artisan.municipality, artisan.state) if p),
        "biography": artisan.biography or "",
        "history": artisan.history or "",
        "techniques": list(artisan.techniques or []),
        "languages": list(artisan.languages or []) if artisan.languages_public else [],
        "public_contact": {k: v for k, v in (artisan.public_contact or {}).items() if v},
        "portrait": f"/media/{portrait.storage_path}" if portrait else None,
        "pieces": [{"name": p.name, "cover": p.cover_media.url if p.cover_media else None} for p in pieces],
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


def _closed_at():
    return func.coalesce(ArtisanAuthorization.revoked_at, ArtisanAuthorization.decided_at)


def last_answer(db: Session, artisan_id: uuid.UUID) -> ArtisanAuthorization | None:
    """The artisan's latest "Quiero cambios" or "No autorizo", while nothing
    newer replaced it (Gestión shows the comment until then). Ordered by
    decided_at/revoked_at (clock_timestamp), not created_at (transaction
    start: two rows of one transaction would tie)."""
    if current(db, artisan_id) is not None:
        return None
    latest = db.execute(select(ArtisanAuthorization).where(ArtisanAuthorization.artisan_id == artisan_id,
                                                           _closed_at().is_not(None))
                        .order_by(_closed_at().desc()).limit(1)).scalar_one_or_none()
    closed = (AuthorizationStatus.changes_requested, AuthorizationStatus.declined)
    return latest if latest is not None and latest.status in closed else None


def request(db: Session, actor: Actor, artisan_id: uuid.UUID) -> tuple[ArtisanAuthorization, str]:
    """A new link. A pending request is replaced (its old link stops working).
    An authorization recorded in person can be confirmed by WhatsApp: it stays
    valid while the link is open."""
    artisan = _artisan(db, artisan_id)
    row = current(db, artisan.id)
    if row is not None and row.status == AuthorizationStatus.authorized and row.medium == "whatsapp":
        raise ContentConflict("already_authorized", "El artesano ya autorizó la publicación.")
    confirming = row is not None and row.status == AuthorizationStatus.authorized
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
           metadata={"authorization_id": str(row.id), "expires_days": LINK_DAYS,
                     **({"confirms": "en persona"} if confirming else {})})
    db.commit()
    return row, token


def by_token(db: Session, token: str, *, for_update: bool = False) -> ArtisanAuthorization | None:
    if not token or len(token) > 100:
        return None
    # Authorized rows carry a token only while a confirmation is asked.
    stmt = select(ArtisanAuthorization).where(ArtisanAuthorization.token_hash == _hash(token),
                                              ArtisanAuthorization.status.in_(_OPEN))
    if for_update:
        stmt = stmt.with_for_update()
    row = db.execute(stmt).scalar_one_or_none()
    if row is None or row.expires_at is None or row.expires_at <= _now(db):
        return None
    return row


def _unpublish_all(db: Session, artisan_id: uuid.UUID, authorization_id: uuid.UUID, ip: str | None) -> list[str]:
    """"No autorizo": the artisan and their published pieces go back to
    draft, audited as done by the link (system actor)."""
    reason = {"reason": "authorization_declined", "authorization_id": str(authorization_id)}
    artisan = db.execute(select(Artisan).where(Artisan.id == artisan_id).with_for_update()).scalar_one()
    done: list[str] = []
    for piece in _published_pieces(db, artisan_id, for_update=True):
        piece.publication_status, piece.updated_at = PublicationStatus.draft, func.clock_timestamp()
        _audit(db, actor=None, artisan_id=piece.id, ip=ip, entity_type="piece", action="unpublished",
               metadata={"from": "published", "to": "draft", **reason})
        done.append(piece.slug)
    if artisan.publication_status == PublicationStatus.published:
        artisan.publication_status, artisan.updated_at = PublicationStatus.draft, func.clock_timestamp()
        _audit(db, actor=None, artisan_id=artisan.id, ip=ip, action="unpublished",
               metadata={"from": "published", "to": "draft", **reason})
        done.append(artisan.slug)
    return done


def decide(db: Session, token: str, *, decision: str, comment: str | None, ip: str | None) -> str:
    """``recorded``, ``unavailable`` (unknown, expired or already answered
    link) or ``comment_required`` ("Quiero cambios" without saying what)."""
    if decision not in DECISIONS:  # pragma: no cover - the router validates it
        raise ValueError(decision)
    note = (comment or "").strip()[:500] or None
    if decision == "changes" and len(note or "") < _MIN_COMMENT:
        return "comment_required"
    row = by_token(db, token, for_update=True)
    if row is None:
        db.rollback()
        return "unavailable"
    confirming = row.status == AuthorizationStatus.authorized  # an in-person one, confirmed now
    row.status = {"authorize": AuthorizationStatus.authorized, "changes": AuthorizationStatus.changes_requested,
                  "decline": AuthorizationStatus.declined}[decision]
    row.medium = "whatsapp"
    row.decided_at, row.decided_ip, row.note = _now(db), ip, note
    row.token_hash, row.expires_at = None, None
    metadata = {"authorization_id": str(row.id), "medium": "whatsapp", **({"confirms": "en persona"} if confirming else {})}
    if note and decision != "authorize":
        metadata["comment"] = note
    if decision == "decline":
        db.flush()
        metadata["unpublished"] = _unpublish_all(db, row.artisan_id, row.id, ip)
    action = {"authorize": "authorized", "changes": "authorization_changes_requested",
              "decline": "authorization_declined"}[decision]
    _audit(db, actor=None, artisan_id=row.artisan_id, ip=ip, action=action, metadata=metadata)
    db.commit()
    return "recorded"


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
