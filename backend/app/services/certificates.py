from __future__ import annotations

import base64
import hashlib
import re
import secrets
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.certificate import Certificate, CertificateStatus
from app.services.lifecycle import (
    LifecycleConflict,
    LifecycleError,
    LifecycleIntegrityError,
    lock_and_reload,
    run_in_savepoint,
    snapshot_and_expire,
)

# SECURITY.md section 2.1 (approved MVP standard): 256 bits of CSPRNG
# randomness, Base64 URL-safe, padding stripped. Deliberately not a UUID
# and not derived from any predictable input (certificate/piece id, NFC
# UID, timestamp) — ADR-007.
_TOKEN_BYTES = 32

T = TypeVar("T")

# ceil(32 bytes * 8 bits / 6 bits per base64 char), unpadded — SECURITY.md
# section 2.1's "valid issued tokens are 43 characters".
TOKEN_LENGTH = 43
_TOKEN_SHAPE_RE = re.compile(rf"^[A-Za-z0-9_-]{{{TOKEN_LENGTH}}}$")


# Constraint identities the service maps deliberately (DATA_MODEL.md section
# 6). Any other constraint failure becomes CertificateLifecycleIntegrityError.
_UQ_ONE_ACTIVE_PER_PIECE = "uq_certificate_one_active_per_piece"

# The lifecycle columns a transition decides on and rewrites. Reloaded under
# lock before every transition (services/lifecycle.py).
_LIFECYCLE_FIELDS = ("status", "token_hash", "issued_at", "revoked_at")


class CertificateServiceError(LifecycleError):
    """Base class for certificate lifecycle service errors."""


class InvalidCertificateTransition(CertificateServiceError):
    """Raised when a lifecycle transition is attempted from an invalid status."""


class ActiveCertificateAlreadyExists(CertificateServiceError):
    """Raised when a piece already has an active certificate."""


class ActiveCertificateNotFound(CertificateServiceError):
    """Raised when an operation requires an active certificate that does not exist."""


class CertificateLifecycleConflict(CertificateServiceError, LifecycleConflict):
    """A concurrent transaction changed this piece's certificate lifecycle, or
    the caller's certificate object was stale. Reload and decide again."""


class CertificateLifecycleIntegrityError(CertificateServiceError, LifecycleIntegrityError):
    """An unexpected constraint failure (including a token_hash collision).
    Never carries database detail, a token or a token hash."""


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


def _active_certificate_for_piece(
    db: Session, piece_id: uuid.UUID, *, for_update: bool = False
) -> Certificate | None:
    """Canonical lookup for "the active certificate of a piece" (DATA_MODEL.md
    section 2.3: `status = 'active'`), shared by activation and rotation.
    `for_update` locks the row and overwrites any stale copy already in the
    session's identity map with the persisted state.
    """
    stmt = select(Certificate).where(
        Certificate.piece_id == piece_id, Certificate.status == CertificateStatus.active
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return db.execute(stmt).scalar_one_or_none()


def _run(
    db: Session,
    work: Callable[[], T],
    *,
    known: Mapping[str, Callable[[], LifecycleError]] | None = None,
) -> T:
    return run_in_savepoint(
        db,
        work,
        conflict=CertificateLifecycleConflict,
        integrity=CertificateLifecycleIntegrityError,
        known=known,
    )


def _lock(db: Session, certificate: Certificate, believed: dict) -> bool:
    return lock_and_reload(
        db, certificate, Certificate, _LIFECYCLE_FIELDS, believed, conflict=CertificateLifecycleConflict
    )


def _reject_state(stale: bool, certificate: Certificate, action: str, required: str) -> None:
    """Invalid transition, or - when the caller's copy was behind the
    database - a stale-state conflict. Messages carry ids and statuses only."""
    status = certificate.status.value
    if stale:
        raise CertificateLifecycleConflict(
            f"Certificate {certificate.id} changed state concurrently (now '{status}'); "
            f"cannot {action}. Reload and retry."
        )
    raise InvalidCertificateTransition(
        f"Certificate {certificate.id} cannot be {action}d from status '{status}'; "
        f"only '{required}' certificates can be {action}d."
    )


def activate_certificate(db: Session, certificate: Certificate) -> CertificateActivationResult:
    """Activate a draft certificate, issuing its one and only raw token.

    Preconditions (DATA_MODEL.md section 2.3): the *persisted* status must be
    `draft` (and therefore `token_hash` is NULL), and the certificate's piece
    must not already have another active certificate. The row is locked and
    reloaded first, so a stale object can never overwrite a newer state: it
    raises `CertificateLifecycleConflict` (stale handle) or
    `InvalidCertificateTransition` (current handle, wrong state).

    The partial unique index on `certificate.piece_id` remains the final,
    authoritative guard for two *different* drafts of one piece racing: the
    loser's constraint failure is rolled back with its SAVEPOINT and raised as
    `ActiveCertificateAlreadyExists` - what a serial run after the winner
    would have produced. The caller owns the commit.
    """
    believed = snapshot_and_expire(db, certificate, _LIFECYCLE_FIELDS)
    piece_id = certificate.piece_id

    def _already_active() -> ActiveCertificateAlreadyExists:
        return ActiveCertificateAlreadyExists(f"Piece {piece_id} already has an active certificate.")

    def work() -> CertificateActivationResult:
        stale = _lock(db, certificate, believed)
        if certificate.status != CertificateStatus.draft:
            _reject_state(stale, certificate, "activate", "draft")

        if _active_certificate_for_piece(db, piece_id) is not None:
            raise _already_active()

        raw_token = generate_certificate_token()
        certificate.token_hash = hash_certificate_token(raw_token)
        certificate.status = CertificateStatus.active
        certificate.issued_at = datetime.now(timezone.utc)
        db.flush()
        return CertificateActivationResult(certificate=certificate, raw_token=raw_token)

    return _run(db, work, known={_UQ_ONE_ACTIVE_PER_PIECE: _already_active})


def issue_certificate(db: Session, piece_id: uuid.UUID) -> CertificateActivationResult:
    """Issue a piece's first (or next, after a revocation) certificate: create
    the draft and activate it as one atomic unit.

    `activate_certificate` needs an existing draft and only `rotate_certificate`
    ever created one, so a piece with no active certificate had no supported
    way to get one. This mirrors rotation's second half: a SAVEPOINT around
    "insert draft -> activate", so a failed activation never leaves an orphan
    draft behind. Every guarantee of `activate_certificate` applies unchanged
    (one active certificate per piece, `raw_token` only in the result). The
    caller owns the commit.
    """

    def work() -> CertificateActivationResult:
        draft = Certificate(piece_id=piece_id, status=CertificateStatus.draft)
        db.add(draft)
        db.flush()
        return activate_certificate(db, draft)

    # No `known` mapping: the activation translates its own unique-index
    # failure (ActiveCertificateAlreadyExists).
    return _run(db, work)


def revoke_certificate(
    db: Session, certificate: Certificate, *, reason: str | None = None
) -> Certificate:
    """Revoke an active certificate, preserving its token_hash/issued_at history.

    Decided on the locked, reloaded persisted state: a stale handle (e.g. the
    certificate was already revoked by a rotation) raises
    `CertificateLifecycleConflict` and leaves `revoked_at` untouched.

    `reason` (optional) is stored as `revocation_reason`, the internal,
    never-public note of DATA_MODEL.md section 2.3. Callers pass a short code,
    never free text that could carry a secret; with `reason=None` the column
    is left exactly as before.
    """
    believed = snapshot_and_expire(db, certificate, _LIFECYCLE_FIELDS)

    def work() -> Certificate:
        stale = _lock(db, certificate, believed)
        if certificate.status != CertificateStatus.active:
            _reject_state(stale, certificate, "revoke", "active")

        certificate.status = CertificateStatus.revoked
        certificate.revoked_at = datetime.now(timezone.utc)
        if reason is not None:
            certificate.revocation_reason = reason
        db.flush()
        return certificate

    return _run(db, work)


def rotate_certificate(
    db: Session, piece_id: uuid.UUID, *, reason: str | None = None
) -> CertificateRotationResult:
    """Revoke the piece's active certificate and activate a brand-new replacement.

    Runs as one atomic unit via a SAVEPOINT (`Session.begin_nested`): if any
    step fails, everything since the start of rotation is rolled back,
    regardless of what the caller's outer transaction does afterwards.

    The active certificate is located with `SELECT ... FOR UPDATE`, which
    serializes rotations of one piece: the second caller blocks until the
    first's transaction ends. Under READ COMMITTED it then finds no active
    row *in that statement's snapshot* even though the winner just issued a
    new one, so on an empty result the lookup is repeated with a fresh
    snapshot: an active certificate now exists -> `CertificateLifecycleConflict`
    (a competitor changed the lifecycle; the loser deliberately does not
    rotate the winner's brand-new token away); none -> the genuine
    `ActiveCertificateNotFound`.

    `reason` (optional) becomes the revoked certificate's `revocation_reason`.
    """

    def work() -> CertificateRotationResult:
        active_certificate = _active_certificate_for_piece(db, piece_id, for_update=True)
        if active_certificate is None:
            if _active_certificate_for_piece(db, piece_id) is not None:
                raise CertificateLifecycleConflict(
                    f"Piece {piece_id} changed certificate state concurrently; reload and retry."
                )
            raise ActiveCertificateNotFound(f"Piece {piece_id} has no active certificate to rotate.")

        revoke_certificate(db, active_certificate, reason=reason)

        new_certificate = Certificate(piece_id=piece_id, status=CertificateStatus.draft)
        db.add(new_certificate)
        db.flush()

        activation = activate_certificate(db, new_certificate)

        return CertificateRotationResult(
            certificate=activation.certificate,
            raw_token=activation.raw_token,
            revoked_certificate=active_certificate,
        )

    # No `known` mapping: the replacement's activation translates its own
    # unique-index failure, and nothing else in this unit can trip one.
    return _run(db, work)
