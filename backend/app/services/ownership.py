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

Owner verification (email codes): a 6-digit code goes to the email registered
in the claim and proves who asks. It is stored as a scrypt hash, expires in
10 minutes, allows 5 tries and works once. Two uses: the owner resets a
forgotten PIN on the public page (card key + code + new PIN), and the
custodian confirms the owner in Gestión before replacing a lost card or
releasing a claim.
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
from app.models.ownership import (
    OwnerVerification,
    OwnerVerificationPurpose,
    OwnershipCard,
    OwnershipCardStatus,
    PieceClaim,
    PieceClaimStatus,
)
from app.models.piece import Piece
from app.services import mailer

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


def _card_for_key(db: Session, certificate: Certificate | None, raw_key: str, ip: str | None
                  ) -> tuple[OwnershipCard, Piece, None] | tuple[None, None, UnlockOutcome]:
    """The shared first half of every public card action: attempt limits, key
    check (every failure is the same ``invalid`` and counts), stolen report.
    Returns the locked card and piece, or the outcome to answer."""
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
        return None, None, _fail(db, card=card, piece_id=piece_id, ip=ip)

    piece = db.get(Piece, certificate.piece_id)
    if piece.reported_stolen_at is not None:
        db.rollback()
        return None, None, UnlockOutcome("reported_stolen")
    return card, piece, None


def unlock(db: Session, certificate: Certificate | None, raw_key: str, pin: str | None, ip: str | None,
           *, claim_email: str | None = None) -> UnlockOutcome:
    """certificate: the active, public certificate the token resolved to (or
    None). With claim_email set, a successful key check creates the claim
    with ``pin`` instead of checking an existing one."""
    card, piece, refused = _card_for_key(db, certificate, raw_key, ip)
    if refused is not None:
        return refused

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


# --- Owner verification: emailed one-time codes --------------------------------------

CODE_TTL = timedelta(minutes=10)
CODE_MAX_ATTEMPTS = 5
CODE_RESEND_GAP = timedelta(seconds=60)
CODE_MAX_PER_HOUR = 5
CUSTODY_VERIFIED_WINDOW = timedelta(minutes=30)

_CODE_SUBJECT = "Tu código de ArtesaNFC"
_CODE_BODY = """Hola.

Tu código de verificación de ArtesaNFC es: {code}

{purpose}

Caduca en 10 minutos y sirve una sola vez. Si no lo pediste tú, ignora este correo:
nadie puede usar tu pieza sin este código.

ArtesaNFC
"""
_PURPOSE_TEXT = {
    OwnerVerificationPurpose.pin_reset: "Lo pediste para elegir un PIN nuevo de tu certificado.",
    OwnerVerificationPurpose.custody: "Dáselo al equipo de ArtesaNFC para confirmar que eres tú.",
}


class CodeRateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(retry_after)
        self.retry_after = retry_after


def _new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def _open_verification(db: Session, claim_id: uuid.UUID, purpose: OwnerVerificationPurpose
                       ) -> OwnerVerification | None:
    return db.execute(
        select(OwnerVerification).where(
            OwnerVerification.claim_id == claim_id,
            OwnerVerification.purpose == purpose,
            OwnerVerification.consumed_at.is_(None),
        ).order_by(OwnerVerification.created_at.desc()).limit(1).with_for_update()
    ).scalar_one_or_none()


def _send_code(db: Session, claim: PieceClaim, purpose: OwnerVerificationPurpose) -> None:
    """Creates a code for the claim's email, replaces any open one and sends
    it. Nothing is stored when the email cannot be sent. Raises
    CodeRateLimited, mailer.MailUnavailable or mailer.MailError."""
    now = _now(db)
    recent = db.execute(
        select(OwnerVerification.created_at).where(
            OwnerVerification.piece_id == claim.piece_id,
            OwnerVerification.created_at >= now - timedelta(hours=1),
        ).order_by(OwnerVerification.created_at.desc())
    ).scalars().all()
    if recent and now - recent[0] < CODE_RESEND_GAP:
        raise CodeRateLimited(int((CODE_RESEND_GAP - (now - recent[0])).total_seconds()) + 1)
    if len(recent) >= CODE_MAX_PER_HOUR:
        raise CodeRateLimited(int((recent[-1] + timedelta(hours=1) - now).total_seconds()) + 1)

    code = _new_code()
    previous = _open_verification(db, claim.id, purpose)
    if previous is not None:
        previous.consumed_at = now
    db.add(OwnerVerification(piece_id=claim.piece_id, claim_id=claim.id, purpose=purpose,
                             code_hash=hash_secret(code), attempts=0, expires_at=now + CODE_TTL))
    db.flush()
    mailer.send_email(claim.owner_email, _CODE_SUBJECT, _CODE_BODY.format(code=code, purpose=_PURPOSE_TEXT[purpose]))


def _check_code(db: Session, claim: PieceClaim, purpose: OwnerVerificationPurpose, code: str
                ) -> OwnerVerification | None:
    """The open verification when ``code`` matches; a wrong code counts as a
    try. None for every failure (no code, expired, too many tries, wrong)."""
    verification = _open_verification(db, claim.id, purpose)
    if verification is None or verification.attempts >= CODE_MAX_ATTEMPTS or verification.expires_at <= _now(db):
        return None
    if not PIN_RE.fullmatch(code or "") or not verify_secret(code, verification.code_hash):
        verification.attempts += 1
        return None
    return verification


def request_pin_reset(db: Session, certificate: Certificate | None, raw_key: str, ip: str | None) -> str:
    """Public: the owner forgot the PIN. With a valid card key, emails a code
    to the claim's address. Answers ``sent`` for every invalid key too, so it
    is no oracle; ``not_claimed`` / ``reported_stolen`` only after a valid
    key. Raises TooManyAttempts, CodeRateLimited, mailer.MailUnavailable."""
    card, piece, refused = _card_for_key(db, certificate, raw_key, ip)
    if refused is not None:
        return "sent" if refused.result == "invalid" else refused.result
    claim = active_claim(db, piece.id)
    if claim is None:
        db.rollback()
        return "not_claimed"
    try:
        _send_code(db, claim, OwnerVerificationPurpose.pin_reset)
    except (CodeRateLimited, mailer.MailUnavailable):
        db.rollback()
        raise
    except mailer.MailError:
        db.rollback()
        return "sent"  # the owner can ask again; the failure is not shown
    _audit(db, action="ownership.pin_reset_requested", result=AuditResult.success, piece_id=piece.id, ip=ip)
    db.commit()
    return "sent"


def confirm_pin_reset(db: Session, certificate: Certificate | None, raw_key: str, code: str, new_pin: str,
                      ip: str | None) -> UnlockOutcome:
    """Public: card key + emailed code + the new PIN. Replaces the PIN and
    opens the original. Every failure is the same ``invalid``."""
    card, piece, refused = _card_for_key(db, certificate, raw_key, ip)
    if refused is not None:
        return refused
    claim = active_claim(db, piece.id)
    verification = _check_code(db, claim, OwnerVerificationPurpose.pin_reset, code) if claim else None
    if verification is None:
        return _fail(db, card=card, piece_id=piece.id, ip=ip)
    verification.consumed_at = _now(db)
    claim.pin_hash = hash_secret(new_pin)
    card.failed_attempts = 0
    card.locked_until = None
    _audit(db, action="ownership.pin_reset", result=AuditResult.success, piece_id=piece.id, ip=ip)
    db.commit()
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


OVERRIDE_NOTE_MIN = 20


def _owner_check(db: Session, claim: PieceClaim | None, override: bool, note: str) -> dict:
    """The email check, or - only for the project owner, who has already been
    authorised by the endpoint - an override with a documented reason (the
    owner lost access to their email). Returns extra audit metadata."""
    if override and claim is not None:
        if len(note.strip()) < OVERRIDE_NOTE_MIN:
            raise OwnershipConflict("override_note_required")
        return {"owner_check_overridden": True}
    _consume_owner_verification(db, claim)
    return {}


def replace_card(db: Session, piece_id: uuid.UUID, note: str, who_email: str, ip: str | None,
                 *, override: bool = False) -> str:
    _piece(db, piece_id)
    extra = _owner_check(db, active_claim(db, piece_id), override, note)
    old = _replace_current(db, piece_id)
    if old is None:
        raise OwnershipConflict("no_card")
    card, key = _new_card(db, piece_id)
    _custody_audit(db, who_email, ip, piece_id, "card_replaced", note,
                   {"card_id": str(card.id), "replaced_card_id": str(old), **extra})
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


def send_owner_code(db: Session, piece_id: uuid.UUID, who_email: str, ip: str | None) -> str:
    """Emails a code to the owner registered in the claim; returns that email.
    The owner reads it to the custodian, who confirms it with verify_owner_code."""
    _piece(db, piece_id)
    claim = active_claim(db, piece_id)
    if claim is None:
        raise OwnershipConflict("not_claimed")
    try:
        _send_code(db, claim, OwnerVerificationPurpose.custody)
    except CodeRateLimited:
        db.rollback()
        raise OwnershipConflict("code_rate_limited") from None
    except mailer.MailUnavailable:
        db.rollback()
        raise OwnershipConflict("mail_unavailable") from None
    except mailer.MailError:
        db.rollback()
        raise OwnershipConflict("mail_failed") from None
    _custody_audit(db, who_email, ip, piece_id, "owner_code_sent", None, {"claim_id": str(claim.id)})
    db.commit()
    return claim.owner_email


def verify_owner_code(db: Session, piece_id: uuid.UUID, code: str, who_email: str, ip: str | None) -> None:
    """The custodian confirms the code the owner read out. For the next
    30 minutes, one replace_card or release_claim may go ahead."""
    _piece(db, piece_id)
    claim = active_claim(db, piece_id)
    if claim is None:
        raise OwnershipConflict("not_claimed")
    verification = _check_code(db, claim, OwnerVerificationPurpose.custody, code.strip())
    if verification is None:
        db.commit()  # keeps the failed try
        raise OwnershipConflict("code_invalid")
    verification.verified_at = _now(db)
    _custody_audit(db, who_email, ip, piece_id, "owner_verified", None, {"claim_id": str(claim.id)})
    db.commit()


def owner_verified_until(db: Session, claim: PieceClaim) -> datetime | None:
    """When the owner's confirmation (by email code) stops serving, or None."""
    verified = db.execute(
        select(func.max(OwnerVerification.verified_at)).where(
            OwnerVerification.claim_id == claim.id,
            OwnerVerification.purpose == OwnerVerificationPurpose.custody,
            OwnerVerification.consumed_at.is_(None),
        )
    ).scalar_one()
    if verified is None or verified + CUSTODY_VERIFIED_WINDOW <= _now(db):
        return None
    return verified + CUSTODY_VERIFIED_WINDOW


def _consume_owner_verification(db: Session, claim: PieceClaim | None) -> None:
    """Gate for the actions that let someone else in: with a claim, the owner
    must have been confirmed by email in the last 30 minutes (works once)."""
    if claim is None:
        return
    now = _now(db)
    verification = db.execute(
        select(OwnerVerification).where(
            OwnerVerification.claim_id == claim.id,
            OwnerVerification.purpose == OwnerVerificationPurpose.custody,
            OwnerVerification.consumed_at.is_(None),
            OwnerVerification.verified_at.is_not(None),
            OwnerVerification.verified_at >= now - CUSTODY_VERIFIED_WINDOW,
        ).order_by(OwnerVerification.verified_at.desc()).limit(1).with_for_update()
    ).scalar_one_or_none()
    if verification is None:
        raise OwnershipConflict("owner_not_verified")
    verification.consumed_at = now


def release_claim(db: Session, piece_id: uuid.UUID, note: str, who_email: str, ip: str | None,
                  *, override: bool = False) -> None:
    _piece(db, piece_id)
    claim = active_claim(db, piece_id)
    if claim is None:
        raise OwnershipConflict("not_claimed")
    extra = _owner_check(db, claim, override, note)
    claim.status = PieceClaimStatus.released
    claim.released_at = _now(db)
    _custody_audit(db, who_email, ip, piece_id, "claim_released", note, {"claim_id": str(claim.id), **extra})
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
