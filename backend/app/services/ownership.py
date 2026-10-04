"""ADR-030 phase 3: the buyer's scratch card, unlocking the original
certificate, claiming the piece, and the custodian's card actions.

Secrets:
- the card key is 50 random bits, shown as 10 Crockford base32 characters
  (``K7QM-4XHT-9R``) once, to the custodian who prints it;
- the claim PIN is 6 digits chosen by the owner;
- both are stored only as salted ``hashlib.scrypt`` hashes (no new
  dependency) and compared in constant time.

Attempt limits (public endpoints):
- per card: after ``CARD_FREE_FAILURES`` failures the card locks for
  ``CARD_BASE_LOCK`` doubling with every further failure, capped at
  ``CARD_MAX_LOCK``. A success resets the counter;
- per IP: at most ``IP_MAX_FAILURES`` failures in ``IP_WINDOW``, counted from
  the append-only audit_event (``ownership.unlock_failed``).
While locked, the key is not even checked, so a lock is no oracle.
Every failure answers the same ``invalid`` result, whatever the cause.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit_event import AuditActorType, AuditEvent, AuditResult
from app.models.certificate import Certificate, CertificateStatus
from app.models.ownership import OwnershipCard, OwnershipCardStatus, PieceClaim, PieceClaimStatus
from app.models.piece import Piece

# --- Secrets ------------------------------------------------------------------

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CARD_KEY_LENGTH = 10  # 10 x 5 bits = 50 bits
_CROCKFORD_ALIASES = str.maketrans({"O": "0", "I": "1", "L": "1"})

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_SCRYPT_PREFIX = "scrypt"

PIN_RE = re.compile(r"[0-9]{6}")
# Trivial PINs are refused at claim time.
_WEAK_PINS = frozenset({"000000", "111111", "222222", "333333", "444444", "555555", "666666",
                        "777777", "888888", "999999", "123456", "654321", "123123", "121212"})
EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}")


def generate_card_key() -> str:
    """The normalized key: 10 Crockford characters, no separators."""
    return "".join(secrets.choice(CROCKFORD) for _ in range(CARD_KEY_LENGTH))


def format_card_key(key: str) -> str:
    return f"{key[:4]}-{key[4:8]}-{key[8:]}"


def normalize_card_key(value: str) -> str | None:
    """Accepts what a person types: any case, dashes or spaces, and the
    Crockford look-alikes O/I/L. None when it cannot be a key."""
    cleaned = re.sub(r"[\s-]", "", value or "").upper().translate(_CROCKFORD_ALIASES)
    if len(cleaned) != CARD_KEY_LENGTH or any(ch not in CROCKFORD for ch in cleaned):
        return None
    return cleaned


def hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(secret.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    b64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731
    return f"{_SCRYPT_PREFIX}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_secret(secret: str, stored: str) -> bool:
    try:
        prefix, n, r, p, salt, digest = stored.split("$")
        if prefix != _SCRYPT_PREFIX:
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(secret.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# A fixed hash to verify against when there is no card, so a missing card
# costs the same time as a wrong key.
_DUMMY_HASH = hash_secret("dummy-ownership-card")


def is_weak_pin(pin: str) -> bool:
    return pin in _WEAK_PINS


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


# --- Attempt limits -------------------------------------------------------------

CARD_FREE_FAILURES = 5
CARD_BASE_LOCK = timedelta(minutes=15)
CARD_MAX_LOCK = timedelta(hours=24)
IP_MAX_FAILURES = 20
IP_WINDOW = timedelta(minutes=15)

UNLOCK_FAILED = "ownership.unlock_failed"
_NO_ENTITY = uuid.UUID(int=0)


def _now(db: Session) -> datetime:
    return db.execute(select(func.clock_timestamp())).scalar_one()


def ip_is_limited(db: Session, ip: str | None) -> bool:
    if not ip:
        return False
    since = _now(db) - IP_WINDOW
    count = db.execute(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == UNLOCK_FAILED,
            AuditEvent.ip_address == ip,
            AuditEvent.occurred_at >= since,
        )
    ).scalar_one()
    return count >= IP_MAX_FAILURES


def _lock_for(failures: int) -> timedelta | None:
    if failures < CARD_FREE_FAILURES:
        return None
    return min(CARD_BASE_LOCK * (2 ** (failures - CARD_FREE_FAILURES)), CARD_MAX_LOCK)


def _audit(db: Session, *, action: str, result: AuditResult, piece_id: uuid.UUID | None, ip: str | None,
           metadata: dict | None = None, actor_email: str | None = None) -> None:
    db.add(AuditEvent(
        occurred_at=func.clock_timestamp(),
        actor_type=AuditActorType.admin_user if actor_email else AuditActorType.system,
        actor_email=actor_email,
        entity_type="piece" if piece_id else "certificate_unlock",
        entity_id=piece_id or _NO_ENTITY,
        action=action,
        result=result,
        ip_address=ip,
        event_metadata=metadata,
    ))


# --- Public: unlock and claim ----------------------------------------------------


class TooManyAttempts(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(retry_after)
        self.retry_after = retry_after


@dataclass(frozen=True)
class UnlockOutcome:
    result: str  # "unlocked" | "pin_required" | "invalid" | "reported_stolen"
    certificate: Certificate | None = None
    card: OwnershipCard | None = None
    claim: PieceClaim | None = None


def current_card(db: Session, piece_id: uuid.UUID, *, for_update: bool = False) -> OwnershipCard | None:
    stmt = select(OwnershipCard).where(
        OwnershipCard.piece_id == piece_id,
        OwnershipCard.status.in_((OwnershipCardStatus.active, OwnershipCardStatus.blocked)),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def active_claim(db: Session, piece_id: uuid.UUID) -> PieceClaim | None:
    return db.execute(
        select(PieceClaim).where(PieceClaim.piece_id == piece_id, PieceClaim.status == PieceClaimStatus.active)
    ).scalar_one_or_none()


def _fail(db: Session, *, card: OwnershipCard | None, piece_id: uuid.UUID | None, ip: str | None) -> UnlockOutcome:
    if card is not None:
        card.failed_attempts += 1
        lock = _lock_for(card.failed_attempts)
        if lock is not None:
            card.locked_until = _now(db) + lock
    _audit(db, action=UNLOCK_FAILED, result=AuditResult.failure, piece_id=piece_id, ip=ip)
    db.commit()
    return UnlockOutcome("invalid")


def unlock(db: Session, certificate: Certificate | None, raw_key: str, pin: str | None, ip: str | None,
           *, claim_email: str | None = None) -> UnlockOutcome:
    """certificate: the active, public certificate the token resolved to (or
    None). With claim_email set, a successful key check creates the claim
    with ``pin`` instead of checking an existing one."""
    if ip_is_limited(db, ip):
        raise TooManyAttempts(int(IP_WINDOW.total_seconds()))
    key = normalize_card_key(raw_key)
    piece_id = certificate.piece_id if certificate else None
    card = current_card(db, piece_id, for_update=True) if piece_id else None

    if card is not None and card.locked_until is not None:
        remaining = (card.locked_until - _now(db)).total_seconds()
        if remaining > 0:
            db.rollback()
            raise TooManyAttempts(int(remaining) + 1)

    key_ok = verify_secret(key or "", card.key_hash if card else _DUMMY_HASH)
    if certificate is None or card is None or key is None or not key_ok or card.status != OwnershipCardStatus.active:
        return _fail(db, card=card, piece_id=piece_id, ip=ip)

    piece = db.get(Piece, certificate.piece_id)
    if piece.reported_stolen_at is not None:
        db.rollback()
        return UnlockOutcome("reported_stolen")

    claim = active_claim(db, piece.id)
    if claim_email is not None:
        if claim is not None:
            # Claimed meanwhile: the owner's PIN is required, never a new claim.
            db.rollback()
            return UnlockOutcome("pin_required")
        claim = PieceClaim(piece_id=piece.id, owner_email=claim_email, pin_hash=hash_secret(pin or ""),
                           status=PieceClaimStatus.active)
        db.add(claim)
        _audit(db, action="ownership.claimed", result=AuditResult.success, piece_id=piece.id, ip=ip)
    elif claim is not None:
        if not pin:
            db.rollback()
            return UnlockOutcome("pin_required")
        if not verify_secret(pin, claim.pin_hash):
            return _fail(db, card=card, piece_id=piece.id, ip=ip)

    card.failed_attempts = 0
    card.locked_until = None
    _audit(db, action="ownership.unlocked", result=AuditResult.success, piece_id=piece.id, ip=ip)
    try:
        db.commit()
    except IntegrityError:
        # Two first claims at the same moment: the unique index lets one in;
        # the other is told the piece already has an owner.
        db.rollback()
        return UnlockOutcome("pin_required")
    return UnlockOutcome("unlocked", certificate=certificate, card=card, claim=claim)


# --- Custodian actions --------------------------------------------------------------


class OwnershipConflict(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _piece(db: Session, piece_id: uuid.UUID) -> Piece:
    piece = db.execute(select(Piece).where(Piece.id == piece_id).with_for_update()).scalar_one()
    return piece


def _custody_audit(db: Session, who_email: str, ip: str | None, piece_id: uuid.UUID, action: str, note: str | None,
                   extra: dict | None = None) -> None:
    metadata = {"note": note} if note else {}
    metadata.update(extra or {})
    _audit(db, action=f"custody.{action}", result=AuditResult.success, piece_id=piece_id, ip=ip,
           metadata=metadata or None, actor_email=who_email)


def _new_card(db: Session, piece_id: uuid.UUID) -> tuple[OwnershipCard, str]:
    key = generate_card_key()
    card = OwnershipCard(piece_id=piece_id, key_hash=hash_secret(key), status=OwnershipCardStatus.active,
                         failed_attempts=0)
    db.add(card)
    db.flush()
    return card, key


def _replace_current(db: Session, piece_id: uuid.UUID) -> uuid.UUID | None:
    card = current_card(db, piece_id, for_update=True)
    if card is None:
        return None
    card.status = OwnershipCardStatus.replaced
    card.replaced_at = _now(db)
    db.flush()
    return card.id


def _require_active_certificate(db: Session, piece_id: uuid.UUID) -> None:
    exists = db.execute(select(Certificate.id).where(
        Certificate.piece_id == piece_id, Certificate.status == CertificateStatus.active)).first()
    if exists is None:
        raise OwnershipConflict("no_active_certificate")


def issue_card(db: Session, piece_id: uuid.UUID, who_email: str, ip: str | None) -> str:
    _piece(db, piece_id)
    _require_active_certificate(db, piece_id)
    if current_card(db, piece_id) is not None:
        raise OwnershipConflict("card_exists")
    card, key = _new_card(db, piece_id)
    _custody_audit(db, who_email, ip, piece_id, "card_issued", None, {"card_id": str(card.id)})
    db.commit()
    return key


def replace_card(db: Session, piece_id: uuid.UUID, note: str, who_email: str, ip: str | None) -> str:
    _piece(db, piece_id)
    old = _replace_current(db, piece_id)
    if old is None:
        raise OwnershipConflict("no_card")
    card, key = _new_card(db, piece_id)
    _custody_audit(db, who_email, ip, piece_id, "card_replaced", note,
                   {"card_id": str(card.id), "replaced_card_id": str(old)})
    db.commit()
    return key


def block_card(db: Session, piece_id: uuid.UUID, note: str, who_email: str, ip: str | None) -> None:
    _piece(db, piece_id)
    card = current_card(db, piece_id, for_update=True)
    if card is None:
        raise OwnershipConflict("no_card")
    if card.status == OwnershipCardStatus.blocked:
        raise OwnershipConflict("card_already_blocked")
    card.status = OwnershipCardStatus.blocked
    card.blocked_at = _now(db)
    _custody_audit(db, who_email, ip, piece_id, "card_blocked", note, {"card_id": str(card.id)})
    db.commit()


def unblock_card(db: Session, piece_id: uuid.UUID, note: str, who_email: str, ip: str | None) -> None:
    """Re-enables a blocked card and clears an attempt lock."""
    _piece(db, piece_id)
    card = current_card(db, piece_id, for_update=True)
    if card is None:
        raise OwnershipConflict("no_card")
    if card.status != OwnershipCardStatus.blocked and card.failed_attempts == 0 and card.locked_until is None:
        raise OwnershipConflict("card_not_blocked")
    card.status = OwnershipCardStatus.active
    card.blocked_at = None
    card.failed_attempts = 0
    card.locked_until = None
    _custody_audit(db, who_email, ip, piece_id, "card_unblocked", note, {"card_id": str(card.id)})
    db.commit()


def release_claim(db: Session, piece_id: uuid.UUID, note: str, who_email: str, ip: str | None) -> None:
    _piece(db, piece_id)
    claim = active_claim(db, piece_id)
    if claim is None:
        raise OwnershipConflict("not_claimed")
    claim.status = PieceClaimStatus.released
    claim.released_at = _now(db)
    _custody_audit(db, who_email, ip, piece_id, "claim_released", note, {"claim_id": str(claim.id)})
    db.commit()


def transfer(db: Session, piece_id: uuid.UUID, note: str, who_email: str, ip: str | None) -> str:
    """New owner: releases the claim (if any) and replaces the card."""
    _piece(db, piece_id)
    claim = active_claim(db, piece_id)
    if claim is not None:
        claim.status = PieceClaimStatus.released
        claim.released_at = _now(db)
    old = _replace_current(db, piece_id)
    if old is None:
        raise OwnershipConflict("no_card")
    card, key = _new_card(db, piece_id)
    _custody_audit(db, who_email, ip, piece_id, "transferred", note,
                   {"card_id": str(card.id), "replaced_card_id": str(old),
                    "released_claim_id": str(claim.id) if claim else None})
    db.commit()
    return key


def set_stolen(db: Session, piece_id: uuid.UUID, stolen: bool, note: str, who_email: str, ip: str | None) -> None:
    piece = _piece(db, piece_id)
    if stolen == (piece.reported_stolen_at is not None):
        raise OwnershipConflict("already_reported_stolen" if stolen else "not_reported_stolen")
    piece.reported_stolen_at = _now(db) if stolen else None
    _custody_audit(db, who_email, ip, piece_id, "reported_stolen" if stolen else "stolen_cleared", note)
    db.commit()
