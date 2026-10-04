"""Gestión phase 2: content writes for artisans and pieces (ADR-029).

Every write:
- runs in the caller's transaction and commits once, with its audit_event in
  the same transaction: either both are stored or neither is;
- locks the row (SELECT ... FOR UPDATE) and compares the caller's
  ``expected_updated_at`` (sent by the UI as If-Match) with the stored value,
  so a change made from a stale screen is refused instead of overwriting
  someone else's edit;
- never deletes here: "archive" is a publication state, and archived
  records can be restored to draft. The Papelera (services/trash.py) is
  separate: a trashed record refuses every write in this module until it
  is restored, and only never-public records can be purged from it;
- never touches certificates or NFC tags (ADR-026: CLI only).

Errors never carry database text (docs/SECURITY.md section 13): unique
violations are mapped to a field name, anything else propagates to the
global database error handler.
"""
from __future__ import annotations

import enum
import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.access import AdminIdentity
from app.core.config import get_settings
from app.models.artisan import Artisan
from app.models.audit_event import AuditActorType, AuditEvent, AuditResult
from app.models.certificate import Certificate, CertificateStatus
from app.models.enums import PublicationStatus
from app.models.piece import AvailabilityStatus, Piece

ARTISAN_FIELDS = (
    "full_name", "artistic_name", "locality", "municipality", "state", "country",
    "languages", "languages_public", "biography", "history", "techniques", "public_contact",
    "validation_whatsapp", "validation_contact_name",
)
# Personal data: the audit records only that it changed (P-026 G3).
_PRIVATE_ARTISAN_FIELDS = ("public_contact", "validation_whatsapp", "validation_contact_name")
PIECE_FIELDS = (
    "name", "description", "history", "technique", "materials", "origin",
    "creation_year", "creation_date", "dimensions", "price_cents", "price_currency",
)
# Public URLs and the code printed with the piece: only editable while the
# record is a draft (never published, or taken back to draft).
DRAFT_ONLY_ARTISAN_FIELDS = ("slug",)
DRAFT_ONLY_PIECE_FIELDS = ("slug", "public_code", "artisan_id")

_UNIQUE_FIELDS = {
    "artisan_slug_key": "slug",
    "piece_slug_key": "slug",
    "piece_public_code_key": "public_code",
}
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
class TransitionAction(str, enum.Enum):
    publish = "publish"
    unpublish = "unpublish"
    archive = "archive"
    restore = "restore"


_PAST = {"publish": "published", "unpublish": "unpublished", "archive": "archived", "restore": "restored"}


class ContentError(Exception):
    status_code = 409

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.field = field


class ContentConflict(ContentError):
    status_code = 409


class StaleWrite(ContentError):
    status_code = 412


class ContentNotFound(ContentError):
    status_code = 404


@dataclass(frozen=True)
class Actor:
    identity: AdminIdentity
    ip_address: str | None


def slugify(text: str, max_length: int = 60) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "registro"


def _unique_slug(db: Session, model: type, base: str) -> str:
    taken = set(db.execute(select(model.slug).where(model.slug.like(f"{base}%"))).scalars())
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _new_public_code(db: Session) -> str:
    while True:
        code = "ANFC-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        if db.execute(select(Piece.id).where(Piece.public_code == code)).first() is None:
            return code


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def _audit(db: Session, actor: Actor, entity_type: str, entity_id: uuid.UUID, action: str, metadata: dict | None) -> None:
    db.add(AuditEvent(
        # The real insert time, not the transaction start (now()): several
        # events of one request keep their order.
        occurred_at=func.clock_timestamp(),
        actor_type=AuditActorType.admin_user,
        actor_email=actor.identity.email,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        result=AuditResult.success,
        ip_address=actor.ip_address,
        event_metadata=metadata,
    ))


def _flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        field = _UNIQUE_FIELDS.get(name or "")
        if field:
            raise ContentConflict("duplicate", f"The {field} is already in use.", field) from None
        raise


def _locked(db: Session, model: type, entity_id: uuid.UUID, expected_updated_at: datetime, kind: str,
            *, allow_trashed: bool = False):
    row = db.execute(select(model).where(model.id == entity_id).with_for_update()).scalar_one_or_none()
    if row is None:
        raise ContentNotFound("not_found", "The requested resource does not exist.")
    if not allow_trashed and getattr(row, "trashed_at", None) is not None:
        raise ContentConflict("trashed", f"This {kind} is in the trash. Restore it first.")
    if row.updated_at != expected_updated_at:
        raise StaleWrite(
            "stale",
            f"This {kind} changed since it was loaded. Reload it and try again.",
        )
    return row


def _apply(row: Any, changes: dict[str, Any], allowed: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    diff: dict[str, dict[str, Any]] = {}
    for field in allowed:
        if field not in changes:
            continue
        before, after = getattr(row, field), changes[field]
        if before != after:
            setattr(row, field, after)
            diff[field] = {"from": _jsonable(before), "to": _jsonable(after)}
    return diff


def _touch(db: Session, row: Any) -> None:
    # clock_timestamp(), not now(): now() is fixed for the whole transaction,
    # so two writes in one transaction would share a version.
    row.updated_at = func.clock_timestamp()


def _finish(db: Session, row: Any) -> None:
    _flush(db)
    db.commit()
    db.refresh(row)


# --- artisans ---------------------------------------------------------------------


def create_artisan(db: Session, actor: Actor, data: dict[str, Any]) -> Artisan:
    slug = data.get("slug") or _unique_slug(db, Artisan, slugify(data["full_name"]))
    artisan = Artisan(slug=slug, publication_status=PublicationStatus.draft,
                      **{k: v for k, v in data.items() if k in ARTISAN_FIELDS})
    db.add(artisan)
    _flush(db)
    _audit(db, actor, "artisan", artisan.id, "artisan.created",
           {"fields": {k: _jsonable(getattr(artisan, k)) for k in ("slug", *ARTISAN_FIELDS)
                       if k not in _PRIVATE_ARTISAN_FIELDS}})
    _finish(db, artisan)
    return artisan


def update_artisan(db: Session, actor: Actor, artisan_id: uuid.UUID, expected: datetime, changes: dict[str, Any]) -> Artisan:
    artisan = _locked(db, Artisan, artisan_id, expected, "artisan")
    if any(f in changes for f in DRAFT_ONLY_ARTISAN_FIELDS) and changes.get("slug") != artisan.slug \
            and artisan.publication_status != PublicationStatus.draft:
        raise ContentConflict("draft_only", "The slug can only change while the artisan is a draft.", "slug")
    diff = _apply(artisan, changes, ARTISAN_FIELDS + DRAFT_ONLY_ARTISAN_FIELDS)
    if diff:
        _touch(db, artisan)
        _audit(db, actor, "artisan", artisan.id, "artisan.updated",
               {"changes": {k: v for k, v in diff.items() if k not in _PRIVATE_ARTISAN_FIELDS}
                | {k: {"changed": True} for k in _PRIVATE_ARTISAN_FIELDS if k in diff}})
    _finish(db, artisan)
    return artisan


def transition_artisan(db: Session, actor: Actor, artisan_id: uuid.UUID, expected: datetime,
                       action: str, reason: str | None = None) -> Artisan:
    artisan = _locked(db, Artisan, artisan_id, expected, "artisan")
    status = artisan.publication_status
    if action == "publish":
        if status != PublicationStatus.draft:
            raise ContentConflict("invalid_transition", "Only a draft can be published.")
        if not (artisan.full_name or "").strip():
            raise ContentConflict("incomplete", "A published artisan needs a full name.", "full_name")
        if get_settings().require_artisan_authorization:
            # P-026 G3: imported here (authorizations imports this module).
            from app.services import authorizations

            if not authorizations.is_authorized(db, artisan.id):
                raise ContentConflict("authorization_missing",
                                      "The artisan has not authorized the publication yet.")
        target = PublicationStatus.published
    elif action == "unpublish":
        if status != PublicationStatus.published:
            raise ContentConflict("invalid_transition", "Only a published artisan can go back to draft.")
        target = PublicationStatus.draft
    elif action == "archive":
        if status == PublicationStatus.archived:
            raise ContentConflict("invalid_transition", "The artisan is already archived.")
        published = db.execute(select(func.count()).select_from(Piece).where(
            Piece.artisan_id == artisan.id, Piece.publication_status == PublicationStatus.published)).scalar_one()
        if published:
            raise ContentConflict("has_published_pieces", "Unpublish or archive this artisan's published pieces first.")
        target = PublicationStatus.archived
    elif action == "restore":
        if status != PublicationStatus.archived:
            raise ContentConflict("invalid_transition", "Only an archived artisan can be restored.")
        target = PublicationStatus.draft
    else:  # pragma: no cover - the router only passes the four actions above
        raise ValueError(action)
    artisan.publication_status = target
    _touch(db, artisan)
    _audit(db, actor, "artisan", artisan.id, f"artisan.{_PAST[action]}",
           {"from": status.value, "to": target.value, **({"reason": reason} if reason else {})})
    _finish(db, artisan)
    return artisan


# --- pieces -------------------------------------------------------------------------


def _require_artisan(db: Session, artisan_id: uuid.UUID) -> Artisan:
    artisan = db.get(Artisan, artisan_id)
    if artisan is None:
        raise ContentConflict("unknown_artisan", "The artisan does not exist.", "artisan_id")
    if artisan.publication_status == PublicationStatus.archived:
        raise ContentConflict("archived_artisan", "Pieces cannot be assigned to an archived artisan.", "artisan_id")
    if artisan.trashed_at is not None:
        raise ContentConflict("trashed_artisan", "Pieces cannot be assigned to an artisan in the trash.", "artisan_id")
    return artisan


def create_piece(db: Session, actor: Actor, data: dict[str, Any]) -> Piece:
    _require_artisan(db, data["artisan_id"])
    if data.get("availability_status") == AvailabilityStatus.sold:
        raise ContentConflict("use_sale", "Mark a piece as sold by registering its sale.", "availability_status")
    piece = Piece(
        artisan_id=data["artisan_id"],
        slug=data.get("slug") or _unique_slug(db, Piece, slugify(data["name"])),
        public_code=data.get("public_code") or _new_public_code(db),
        availability_status=AvailabilityStatus(data.get("availability_status") or "available"),
        publication_status=PublicationStatus.draft,
        **{k: v for k, v in data.items() if k in PIECE_FIELDS},
    )
    db.add(piece)
    _flush(db)
    _audit(db, actor, "piece", piece.id, "piece.created",
           {"fields": {k: _jsonable(getattr(piece, k)) for k in ("slug", "public_code", "artisan_id", "availability_status", *PIECE_FIELDS)}})
    _finish(db, piece)
    return piece


def update_piece(db: Session, actor: Actor, piece_id: uuid.UUID, expected: datetime, changes: dict[str, Any]) -> Piece:
    piece = _locked(db, Piece, piece_id, expected, "piece")
    draft_only = [f for f in DRAFT_ONLY_PIECE_FIELDS if f in changes and changes[f] != getattr(piece, f)]
    if draft_only and piece.publication_status != PublicationStatus.draft:
        raise ContentConflict("draft_only", f"{draft_only[0]} can only change while the piece is a draft.", draft_only[0])
    if "artisan_id" in draft_only:
        _require_artisan(db, changes["artisan_id"])
    diff = _apply(piece, changes, PIECE_FIELDS + DRAFT_ONLY_PIECE_FIELDS)
    if diff:
        _touch(db, piece)
        _audit(db, actor, "piece", piece.id, "piece.updated", {"changes": diff})
    _finish(db, piece)
    return piece


def transition_piece(db: Session, actor: Actor, piece_id: uuid.UUID, expected: datetime,
                     action: str, reason: str | None = None) -> Piece:
    piece = _locked(db, Piece, piece_id, expected, "piece")
    status = piece.publication_status
    if action == "publish":
        if status != PublicationStatus.draft:
            raise ContentConflict("invalid_transition", "Only a draft can be published.")
        artisan = db.get(Artisan, piece.artisan_id)
        if artisan.publication_status == PublicationStatus.archived:
            raise ContentConflict("archived_artisan", "The piece's artisan is archived.", "artisan_id")
        if not (piece.name or "").strip():
            raise ContentConflict("incomplete", "A published piece needs a name.", "name")
        target = PublicationStatus.published
    elif action == "unpublish":
        if status != PublicationStatus.published:
            raise ContentConflict("invalid_transition", "Only a published piece can go back to draft.")
        target = PublicationStatus.draft
    elif action == "archive":
        if status == PublicationStatus.archived:
            raise ContentConflict("invalid_transition", "The piece is already archived.")
        active = db.execute(select(func.count()).select_from(Certificate).where(
            Certificate.piece_id == piece.id, Certificate.status == CertificateStatus.active)).scalar_one()
        if active:
            raise ContentConflict("active_certificate",
                                  "Revoke the piece's active certificate with the provisioning CLI first (ADR-026).")
        target = PublicationStatus.archived
    elif action == "restore":
        if status != PublicationStatus.archived:
            raise ContentConflict("invalid_transition", "Only an archived piece can be restored.")
        target = PublicationStatus.draft
    else:  # pragma: no cover
        raise ValueError(action)
    piece.publication_status = target
    _touch(db, piece)
    _audit(db, actor, "piece", piece.id, f"piece.{_PAST[action]}",
           {"from": status.value, "to": target.value, **({"reason": reason} if reason else {})})
    _finish(db, piece)
    return piece


def set_availability(db: Session, actor: Actor, piece_id: uuid.UUID, expected: datetime,
                     availability: AvailabilityStatus) -> Piece:
    piece = _locked(db, Piece, piece_id, expected, "piece")
    before = piece.availability_status
    # P-026 G1: "sold" comes only from registering a sale, and a sold piece
    # changes back only by cancelling it, so a sale is never lost.
    if availability == AvailabilityStatus.sold:
        raise ContentConflict("use_sale", "Mark a piece as sold by registering its sale.", "availability_status")
    if before == AvailabilityStatus.sold and availability != before:
        raise ContentConflict("use_sale", "A sold piece changes by cancelling its sale.", "availability_status")
    if before != availability:
        piece.availability_status = availability
        _touch(db, piece)
        _audit(db, actor, "piece", piece.id, "piece.availability_changed", {"from": before.value, "to": availability.value})
    _finish(db, piece)
    return piece
