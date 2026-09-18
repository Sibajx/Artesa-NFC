from __future__ import annotations

import base64
import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.certificate import Certificate, CertificateStatus

# SECURITY.md section 2.1 (approved MVP standard): 256 bits of CSPRNG
# randomness, Base64 URL-safe, padding stripped. Deliberately not a UUID
# and not derived from any predictable input (certificate/piece id, NFC
# UID, timestamp) — ADR-007.
_TOKEN_BYTES = 32

# ceil(32 bytes * 8 bits / 6 bits per base64 char), unpadded — SECURITY.md
# section 2.1's "valid issued tokens are 43 characters".
TOKEN_LENGTH = 43
_TOKEN_SHAPE_RE = re.compile(rf"^[A-Za-z0-9_-]{{{TOKEN_LENGTH}}}$")


class CertificateServiceError(Exception):
    """Base class for certificate lifecycle service errors."""


class InvalidCertificateTransition(CertificateServiceError):
    """Raised when a lifecycle transition is attempted from an invalid status."""


class ActiveCertificateAlreadyExists(CertificateServiceError):
    """Raised when a piece already has an active certificate."""


class ActiveCertificateNotFound(CertificateServiceError):
    """Raised when an operation requires an active certificate that does not exist."""


@dataclass(frozen=True)
class CertificateActivationResult:
    """Result of activating a draft certificate.

    ``raw_token`` is the only place the plaintext token is ever available —
    it is never persisted, attached to `certificate`, logged, or included in
    any exception message (SECURITY.md section 2, section 12.2).

    ``repr=False`` on ``raw_token`` is deliberate and load-bearing: Python
    dataclasses include every field in the generated ``__repr__``/``__str__``
    by default, which would otherwise print the bearer secret anywhere this
    result gets logged, printed, or shown in a debugger/traceback.
    """

    certificate: Certificate
    raw_token: str = field(repr=False)


@dataclass(frozen=True)
class CertificateRotationResult:
    """Result of rotating a piece's active certificate.

    Same ``repr=False`` rationale as `CertificateActivationResult` — this
    class does not nest that result object, it copies its plain `certificate`
    and `raw_token` values directly, so there is no indirect path back to the
    secret through a nested dataclass repr either.
    """

    certificate: Certificate
    raw_token: str = field(repr=False)
    revoked_certificate: Certificate


def generate_certificate_token() -> str:
    """Generate a fresh 256-bit CSPRNG token, Base64 URL-safe, unpadded."""
    return base64.urlsafe_b64encode(secrets.token_bytes(_TOKEN_BYTES)).decode("ascii").rstrip("=")


def is_syntactically_plausible_token(token: str) -> bool:
    """Cheap pre-hash/pre-DB shape check: exact length + Base64 URL-safe
    alphabet (SECURITY.md section 4.1's "longitud/alfabeto correctos").

    This is an efficiency shortcut only, never a source of a distinguishable
    public outcome: a token that fails this check and a syntactically
    plausible-but-unknown token must both resolve to the exact same
    `unavailable` result (API_CONTRACT.md section 7, SECURITY.md section
    4.1) — this function only decides whether it is worth spending a hash
    + indexed DB lookup to find that out.
    """
    return bool(_TOKEN_SHAPE_RE.fullmatch(token))


def hash_certificate_token(token: str) -> str:
    """Canonical raw-token -> storage representation (SECURITY.md section 3.1).

    SHA-256 over the UTF-8 bytes of the token, stored as a hex digest —
    matching `Certificate.token_hash`'s `Text` column (DATA_MODEL.md
    section 2.3). No pepper/HMAC: the token already carries 256 bits of
    CSPRNG entropy (SECURITY.md section 3.1).
    """
    if not isinstance(token, str):
        raise TypeError("token must be a str")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def activate_certificate(db: Session, certificate: Certificate) -> CertificateActivationResult:
    """Activate a draft certificate, issuing its one and only raw token.

    Preconditions (DATA_MODEL.md section 2.3): `certificate.status` must be
    `draft` (and therefore `token_hash` is already NULL), and the
    certificate's piece must not already have another active certificate.
    The partial unique index on `certificate.piece_id` remains the final,
    authoritative guard against a concurrent/racing activation for the same
    piece; this application-level check only produces a clearer error in
    the common, non-racing case.
    """
    if certificate.status != CertificateStatus.draft:
        raise InvalidCertificateTransition(
            f"Certificate {certificate.id} cannot be activated from status "
            f"'{certificate.status.value}'; only 'draft' certificates can be activated."
        )

    existing_active = db.execute(
        select(Certificate.id).where(
            Certificate.piece_id == certificate.piece_id,
            Certificate.status == CertificateStatus.active,
        )
    ).first()
    if existing_active is not None:
        raise ActiveCertificateAlreadyExists(
            f"Piece {certificate.piece_id} already has an active certificate."
        )

    raw_token = generate_certificate_token()
    certificate.token_hash = hash_certificate_token(raw_token)
    certificate.status = CertificateStatus.active
    certificate.issued_at = datetime.now(timezone.utc)
    db.flush()

    return CertificateActivationResult(certificate=certificate, raw_token=raw_token)


def revoke_certificate(db: Session, certificate: Certificate) -> Certificate:
    """Revoke an active certificate, preserving its token_hash/issued_at history."""
    if certificate.status != CertificateStatus.active:
        raise InvalidCertificateTransition(
            f"Certificate {certificate.id} cannot be revoked from status "
            f"'{certificate.status.value}'; only 'active' certificates can be revoked."
        )

    certificate.status = CertificateStatus.revoked
    certificate.revoked_at = datetime.now(timezone.utc)
    db.flush()

    return certificate


def rotate_certificate(db: Session, piece_id: uuid.UUID) -> CertificateRotationResult:
    """Revoke the piece's active certificate and activate a brand-new replacement.

    Runs as one atomic unit via a SAVEPOINT (`Session.begin_nested`): if any
    step fails, everything since the start of rotation is rolled back,
    regardless of what the caller's outer transaction does afterwards.

    The existing active certificate is located with `SELECT ... FOR UPDATE`.
    This is the one piece of row-level locking used in this issue, and it is
    needed for a real correctness reason, not defensively: two concurrent
    `rotate_certificate` calls for the *same* piece would otherwise both read
    the same active certificate before either commits, and both would then
    try to revoke it and issue their own replacement. The partial unique
    index still guarantees at most one row ends up `active`, but the loser
    would revoke a certificate that was no longer the "current" one from its
    own point of view, and the operation's own preconditions (must find an
    active certificate) would not have caught that at read time. Locking the
    row serializes rotations for a given piece: the second caller blocks
    until the first's transaction ends, then re-reads and correctly sees
    either no active certificate (if the first succeeded) or the same one
    (if the first rolled back).
    """
    with db.begin_nested():
        active_certificate = db.execute(
            select(Certificate)
            .where(
                Certificate.piece_id == piece_id,
                Certificate.status == CertificateStatus.active,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if active_certificate is None:
            raise ActiveCertificateNotFound(
                f"Piece {piece_id} has no active certificate to rotate."
            )

        revoke_certificate(db, active_certificate)

        new_certificate = Certificate(piece_id=piece_id, status=CertificateStatus.draft)
        db.add(new_certificate)
        db.flush()

        activation = activate_certificate(db, new_certificate)

    return CertificateRotationResult(
        certificate=activation.certificate,
        raw_token=activation.raw_token,
        revoked_certificate=active_certificate,
    )
