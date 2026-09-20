"""Orchestration for certificate issuance and NFC provisioning (issue N-09).

The building blocks already exist and are hardened: `services.certificates`
(issue/rotate/revoke) and `services.nfc_tags` (register/assign/program/lock/
retire). This module only sequences them for the operator CLI
(`app/cli/provision.py`) and adds what neither owns: piece lookup by
`public_code`, eligibility rules, the certificate URL, the non-secret
operation record, and the in-process authenticity self-check.

Contract:

* No terminal I/O and no commit - every `execute_*` function runs inside the
  caller's transaction, so the CLI decides its checkpoints (and a failure
  anywhere in one step rolls that whole step back).
* Every `execute_*` first locks the piece row (`SELECT ... FOR UPDATE`) and
  re-derives the piece state inside the write transaction, so a decision made
  on an earlier read-only preflight is re-validated, and two provisioning runs
  for one piece serialize.
* `raw_token` exists only on `IssueResult`/`RotateResult` (``repr=False``).
  Nothing here logs, stores, or puts it in an exception. What IS stored is
  non-secret: ids, the normalized UID, a reason code and timestamps
  (`nfc_tag.notes`, `certificate.revocation_reason`). AUDIT_EVENT is
  deliberately not part of the pilot (docs/SECURITY.md section 12.1 gap,
  accepted in issue #107).
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.certificates import resolve_certificate
from app.core.config import PRODUCTION_FRONTEND_ORIGIN
from app.core.db_safety import ENV_PRODUCTION
from app.models.artisan import Artisan
from app.models.certificate import Certificate, CertificateStatus
from app.models.enums import PublicationStatus
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.models.piece import Piece
from app.schemas.certificate import CertificateResolveAuthentic, CertificateResolveRequest
from app.services.certificates import (
    TOKEN_LENGTH,
    is_syntactically_plausible_token,
    issue_certificate,
    revoke_certificate,
    rotate_certificate,
)
from app.services.nfc_tags import (
    assign_nfc_tag,
    lock_nfc_tag,
    normalize_physical_uid,
    program_nfc_tag,
    register_nfc_tag,
    retire_nfc_tag,
)

PROVISIONING_VERSION = "1"

# Internal, never-public `certificate.revocation_reason` codes. A fixed set on
# purpose: a free-text field is where a token pasted by mistake would end up.
REVOCATION_REASONS = (
    "lost",
    "damaged",
    "compromised",
    "wrong-tag",
    "replaced",
    "not-deployed",
    "other",
)

# Local/test URLs are a rehearsal only. A tag must never be written with one:
# the base is fixed here (never read from the environment) so it cannot be
# pointed at a wrong host by a typo.
REHEARSAL_URL_BASE = "http://127.0.0.1:5500/c/"

# Anything this long and made only of Base64url characters could be a token
# (43 chars) or a SHA-256 hex digest (64): refused in stored text. UUIDs
# (36 chars, hyphenated) pass.
_SECRET_SHAPED_RE = re.compile(r"[A-Za-z0-9_-]{40,}")
_OPERATOR_RE = re.compile(r"[A-Za-z0-9_.-]{1,32}")


class ProvisioningError(Exception):
    """Base class for provisioning-layer errors. Messages carry only codes,
    ids and statuses - never a token, hash, URL or database detail."""


class PreconditionFailed(ProvisioningError):
    """The piece/tag/certificate state does not allow the operation. `codes`
    are stable identifiers the CLI words for the operator; nothing was
    changed by the failing step."""

    def __init__(self, codes: Sequence[str]) -> None:
        self.codes = tuple(codes)
        super().__init__(", ".join(self.codes))


# --- URL ---------------------------------------------------------------------


def is_rehearsal_environment(app_env: str) -> bool:
    return app_env != ENV_PRODUCTION


def certificate_url_base(app_env: str) -> str:
    """`https://artesanfc.com/c/` in production (the static frontend on
    Cloudflare Pages, never the `api.` host: a token must not reach the API
    origin's access log through a path), a marked local rehearsal base
    otherwise."""
    if app_env == ENV_PRODUCTION:
        return f"{PRODUCTION_FRONTEND_ORIGIN}/c/"
    return REHEARSAL_URL_BASE


def build_certificate_url(app_env: str, raw_token: str) -> str:
    if not isinstance(raw_token, str) or not is_syntactically_plausible_token(raw_token):
        # Static text: the rejected value could be the secret itself.
        raise ValueError(f"A certificate token has exactly {TOKEN_LENGTH} Base64url characters.")
    return certificate_url_base(app_env) + raw_token


# --- State -------------------------------------------------------------------


@dataclass(frozen=True)
class CertificateInfo:
    id: uuid.UUID
    issued_at: datetime | None


@dataclass(frozen=True)
class TagInfo:
    id: uuid.UUID
    physical_uid: str | None
    status: NfcTagStatus
    programmed_at: datetime | None
    locked_at: datetime | None


@dataclass(frozen=True)
class PieceState:
    """Plain values (no ORM objects), so a state read in one transaction can
    be shown and reasoned about after that transaction is gone."""

    piece_id: uuid.UUID
    public_code: str
    slug: str
    name: str
    artisan_name: str
    piece_published: bool
    artisan_published: bool
    active_certificate: CertificateInfo | None
    revoked_certificates: int
    # Non-terminal tags of this piece: available (assigned), programmed, locked.
    tags: tuple[TagInfo, ...]

    @property
    def programmed_tags(self) -> tuple[TagInfo, ...]:
        return tuple(t for t in self.tags if t.status == NfcTagStatus.programmed)

    @property
    def locked_tags(self) -> tuple[TagInfo, ...]:
        return tuple(t for t in self.tags if t.status == NfcTagStatus.locked)


def load_piece_state(db: Session, public_code: str, *, lock: bool = False) -> PieceState | None:
    """The piece identified by its exact `public_code`, or None.

    `lock=True` takes `SELECT ... FOR UPDATE` on the piece row (never in a
    read-only transaction, where PostgreSQL forbids it) and refreshes any
    stale copy in the session."""
    stmt = select(Piece).where(Piece.public_code == public_code.strip())
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    piece = db.execute(stmt).scalar_one_or_none()
    if piece is None:
        return None
    artisan = db.get(Artisan, piece.artisan_id)

    active = db.execute(
        select(Certificate).where(
            Certificate.piece_id == piece.id, Certificate.status == CertificateStatus.active
        )
    ).scalar_one_or_none()
    revoked = db.execute(
        select(func.count())
        .select_from(Certificate)
        .where(Certificate.piece_id == piece.id, Certificate.status == CertificateStatus.revoked)
    ).scalar_one()
    tags = (
        db.execute(
            select(NfcTag)
            .where(
                NfcTag.piece_id == piece.id,
                NfcTag.status.in_(
                    (NfcTagStatus.available, NfcTagStatus.programmed, NfcTagStatus.locked)
                ),
            )
            .order_by(NfcTag.created_at, NfcTag.id)
        )
        .scalars()
        .all()
    )
    return PieceState(
        piece_id=piece.id,
        public_code=piece.public_code,
        slug=piece.slug,
        name=piece.name,
        artisan_name=artisan.full_name,
        piece_published=piece.publication_status == PublicationStatus.published,
        artisan_published=artisan.publication_status == PublicationStatus.published,
        active_certificate=(
            CertificateInfo(id=active.id, issued_at=active.issued_at) if active is not None else None
        ),
        revoked_certificates=revoked,
        tags=tuple(
            TagInfo(
                id=t.id,
                physical_uid=t.physical_uid,
                status=t.status,
                programmed_at=t.programmed_at,
                locked_at=t.locked_at,
            )
            for t in tags
        ),
    )


def physical_uid_registered(db: Session, physical_uid: str) -> bool:
    """Whether a tag with this (already normalized) UID exists in any status:
    rows are never deleted, so a retired tag's UID stays taken."""
    return db.execute(select(NfcTag.id).where(NfcTag.physical_uid == physical_uid)).first() is not None


def list_public_codes(db: Session) -> list[str]:
    return list(db.execute(select(Piece.public_code).order_by(Piece.public_code)).scalars().all())


# --- Eligibility ---------------------------------------------------------------
#
# Stable codes; the CLI words them in Spanish. An empty list means allowed.

PIECE_NOT_FOUND = "piece_not_found"
PIECE_NOT_PUBLISHED = "piece_not_published"
ARTISAN_NOT_PUBLISHED = "artisan_not_published"
ACTIVE_CERTIFICATE_EXISTS = "active_certificate_exists"
NO_ACTIVE_CERTIFICATE = "no_active_certificate"
TAG_IN_USE = "tag_in_use"
SAME_TAG_UNAVAILABLE = "same_tag_unavailable"
TAG_LOCKED_REQUIRES_NEW_TAG = "tag_locked_requires_new_tag"
TAG_ALREADY_LOCKED = "tag_already_locked"
NO_PROGRAMMED_TAG = "no_programmed_tag"
TAG_NOT_AVAILABLE = "tag_not_available"
INVALID_REASON = "invalid_reason"


def _publication_blockers(state: PieceState) -> list[str]:
    # A certificate of an unpublished piece/artisan resolves as `unavailable`
    # (API_CONTRACT.md section 9), so its tag could never be verified.
    codes = []
    if not state.piece_published:
        codes.append(PIECE_NOT_PUBLISHED)
    if not state.artisan_published:
        codes.append(ARTISAN_NOT_PUBLISHED)
    return codes


def issue_blockers(state: PieceState) -> list[str]:
    codes = _publication_blockers(state)
    if state.active_certificate is not None:
        codes.append(ACTIVE_CERTIFICATE_EXISTS)
    if state.tags:
        codes.append(TAG_IN_USE)
    return codes


def rotate_blockers(state: PieceState) -> list[str]:
    codes = _publication_blockers(state)
    if state.active_certificate is None:
        codes.append(NO_ACTIVE_CERTIFICATE)
    return codes


def revoke_blockers(state: PieceState) -> list[str]:
    return [] if state.active_certificate is not None else [NO_ACTIVE_CERTIFICATE]


def lock_blockers(state: PieceState) -> list[str]:
    codes = []
    if state.active_certificate is None:
        codes.append(NO_ACTIVE_CERTIFICATE)
    if state.locked_tags:
        codes.append(TAG_ALREADY_LOCKED)
    elif len(state.programmed_tags) != 1:
        codes.append(NO_PROGRAMMED_TAG)
    return codes


def recommended_action(state: PieceState) -> str:
    """Stable code for `status`: what the operator should do next."""
    if state.active_certificate is None:
        return "revoked_with_tags" if state.tags else "issue"
    if state.locked_tags:
        return "locked"
    if state.programmed_tags:
        return "verify_then_optional_lock"
    return "interrupted_rotate"


# --- Non-secret metadata ---------------------------------------------------------


def _operator_label(operator: str) -> str:
    return operator if _OPERATOR_RE.fullmatch(operator or "") else "unknown"


def operation_record(
    action: str,
    public_code: str,
    *,
    operator: str,
    certificate_id: uuid.UUID | None = None,
    tag_id: uuid.UUID | None = None,
    physical_uid: str | None = None,
    reason: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> str:
    """One line of non-secret provenance: what was done, to which piece, by
    which OS user, when. Stored in `nfc_tag.notes` and printed for the
    operator's journal. Never a token, URL or hash."""
    moment = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [moment, action, f"piece={public_code}"]
    if certificate_id is not None:
        parts.append(f"cert={certificate_id}")
    if tag_id is not None:
        parts.append(f"tag={tag_id}")
    if physical_uid is not None:
        parts.append(f"uid={physical_uid}")
    if reason is not None:
        parts.append(f"reason={reason}")
    if note is not None:
        parts.append(f"note={note}")
    parts.append(f"by={_operator_label(operator)}")
    parts.append(f"cli={PROVISIONING_VERSION}")
    return " ".join(parts)


def _append_note(tag: NfcTag, line: str) -> None:
    if _SECRET_SHAPED_RE.search(line):
        raise ValueError("Refusing to store secret-shaped text in nfc_tag.notes.")
    tag.notes = f"{tag.notes}\n{line}" if tag.notes else line


def _validate_reason(reason: str) -> None:
    if reason not in REVOCATION_REASONS:
        raise PreconditionFailed([INVALID_REASON])


# --- Results ---------------------------------------------------------------------


@dataclass(frozen=True)
class IssueResult:
    """`raw_token` is the only place the secret is available; `repr=False`
    keeps it out of any repr/str/traceback of this result."""

    certificate_id: uuid.UUID
    tag_id: uuid.UUID
    physical_uid: str
    record: str
    raw_token: str = field(repr=False)


@dataclass(frozen=True)
class RotateResult:
    certificate_id: uuid.UUID
    revoked_certificate_id: uuid.UUID
    tag_id: uuid.UUID
    physical_uid: str | None
    # True when the tag is still `available` (new tag, or an interrupted
    # issue) and must be marked programmed after the write is confirmed.
    needs_program: bool
    retired_tag_ids: tuple[uuid.UUID, ...]
    record: str
    raw_token: str = field(repr=False)


@dataclass(frozen=True)
class RevokeResult:
    certificate_id: uuid.UUID
    retired_tag_ids: tuple[uuid.UUID, ...]
    record: str


@dataclass(frozen=True)
class TagStepResult:
    tag_id: uuid.UUID
    physical_uid: str | None
    record: str


# --- Steps -------------------------------------------------------------------------


def _locked_state(db: Session, public_code: str) -> PieceState:
    state = load_piece_state(db, public_code, lock=True)
    if state is None:
        raise PreconditionFailed([PIECE_NOT_FOUND])
    return state


def _retire_tags(db: Session, state: PieceState, *, action_note: str, operator: str) -> tuple[uuid.UUID, ...]:
    retired: list[uuid.UUID] = []
    for info in state.tags:
        tag = db.get(NfcTag, info.id)
        retire_nfc_tag(db, tag)
        _append_note(
            tag,
            operation_record(
                "retire", state.public_code, operator=operator, tag_id=tag.id, note=action_note
            ),
        )
        retired.append(tag.id)
    return tuple(retired)


def execute_issue(db: Session, public_code: str, physical_uid: str, *, operator: str) -> IssueResult:
    """register tag + assign tag + issue certificate, in the caller's single
    transaction (decision E of issue #107): all of it or none of it."""
    uid = normalize_physical_uid(physical_uid)  # before touching the database
    state = _locked_state(db, public_code)
    blockers = issue_blockers(state)
    if blockers:
        raise PreconditionFailed(blockers)

    piece = db.get(Piece, state.piece_id)
    tag = register_nfc_tag(db, physical_uid=uid)
    assign_nfc_tag(db, tag, piece)
    activation = issue_certificate(db, piece.id)

    record = operation_record(
        "issue",
        state.public_code,
        operator=operator,
        certificate_id=activation.certificate.id,
        tag_id=tag.id,
        physical_uid=tag.physical_uid,
        note="awaiting-write",
    )
    _append_note(tag, record)
    return IssueResult(
        certificate_id=activation.certificate.id,
        tag_id=tag.id,
        physical_uid=tag.physical_uid,
        record=record,
        raw_token=activation.raw_token,
    )


def execute_rotate(
    db: Session,
    public_code: str,
    *,
    reason: str,
    new_physical_uid: str | None,
    operator: str,
) -> RotateResult:
    """Revoke the active certificate and issue a new one, plus the tag side:

    * `new_physical_uid=None` - rewrite the SAME tag (allowed while it is
      `available` or `programmed`, never `locked`).
    * a UID - a NEW tag: every non-terminal tag of the piece is retired, the
      new one is registered and assigned. It stays `available`; `program` is
      recorded only after the operator confirms the physical write (decision
      F). `replace_nfc_tag` is deliberately not used: it would mark the new tag
      `programmed` before anything was written (decision I).
    """
    _validate_reason(reason)
    uid = normalize_physical_uid(new_physical_uid) if new_physical_uid is not None else None
    state = _locked_state(db, public_code)
    blockers = rotate_blockers(state)
    if blockers:
        raise PreconditionFailed(blockers)

    piece = db.get(Piece, state.piece_id)
    retired: tuple[uuid.UUID, ...] = ()
    if uid is None:
        if len(state.tags) != 1:
            raise PreconditionFailed([SAME_TAG_UNAVAILABLE])
        tag = db.get(NfcTag, state.tags[0].id)
        if tag.status == NfcTagStatus.locked:
            raise PreconditionFailed([TAG_LOCKED_REQUIRES_NEW_TAG])
    else:
        # Register first: a duplicate UID fails before anything else changes.
        tag = register_nfc_tag(db, physical_uid=uid)
        retired = _retire_tags(db, state, action_note=f"replaced-by-rotate reason={reason}", operator=operator)
        assign_nfc_tag(db, tag, piece)

    rotation = rotate_certificate(db, piece.id, reason=reason)
    record = operation_record(
        "rotate",
        state.public_code,
        operator=operator,
        certificate_id=rotation.certificate.id,
        tag_id=tag.id,
        physical_uid=tag.physical_uid,
        reason=reason,
        note="awaiting-write",
    )
    _append_note(tag, record)
    return RotateResult(
        certificate_id=rotation.certificate.id,
        revoked_certificate_id=rotation.revoked_certificate.id,
        tag_id=tag.id,
        physical_uid=tag.physical_uid,
        needs_program=tag.status == NfcTagStatus.available,
        retired_tag_ids=retired,
        record=record,
        raw_token=rotation.raw_token,
    )


def execute_revoke(db: Session, public_code: str, *, reason: str, operator: str) -> RevokeResult:
    """Revoke the active certificate and retire the piece's tags.

    Retiring is not optional: with the certificate revoked, the URL on the
    tag resolves as `unavailable` for good, so the tag is functionally dead.
    Leaving it `programmed` would only make the piece un-issuable (`issue`
    refuses a piece that still has a tag in use)."""
    _validate_reason(reason)
    state = _locked_state(db, public_code)
    blockers = revoke_blockers(state)
    if blockers:
        raise PreconditionFailed(blockers)

    certificate = db.get(Certificate, state.active_certificate.id)
    revoke_certificate(db, certificate, reason=reason)
    retired = _retire_tags(db, state, action_note=f"retired-by-revoke reason={reason}", operator=operator)
    record = operation_record(
        "revoke", state.public_code, operator=operator, certificate_id=certificate.id, reason=reason
    )
    return RevokeResult(certificate_id=certificate.id, retired_tag_ids=retired, record=record)


def execute_program(db: Session, public_code: str, tag_id: uuid.UUID, *, operator: str) -> TagStepResult:
    """Record `available -> programmed`. Call it only after the operator
    confirmed the tag was physically written AND read back correctly
    (decision F)."""
    state = _locked_state(db, public_code)
    if state.active_certificate is None:
        raise PreconditionFailed([NO_ACTIVE_CERTIFICATE])
    if not any(t.id == tag_id and t.status == NfcTagStatus.available for t in state.tags):
        raise PreconditionFailed([TAG_NOT_AVAILABLE])

    tag = db.get(NfcTag, tag_id)
    program_nfc_tag(db, tag)
    record = operation_record(
        "program",
        state.public_code,
        operator=operator,
        certificate_id=state.active_certificate.id,
        tag_id=tag.id,
        physical_uid=tag.physical_uid,
        note="write-and-readback-confirmed",
    )
    _append_note(tag, record)
    return TagStepResult(tag_id=tag.id, physical_uid=tag.physical_uid, record=record)


def execute_rewrite_note(db: Session, public_code: str, tag_id: uuid.UUID, *, operator: str) -> TagStepResult:
    """Provenance for rewriting an already `programmed` tag after a rotation:
    the tag's status does not change, so only a note records it."""
    state = _locked_state(db, public_code)
    if state.active_certificate is None:
        raise PreconditionFailed([NO_ACTIVE_CERTIFICATE])
    tag = db.get(NfcTag, tag_id)
    record = operation_record(
        "rewrite",
        state.public_code,
        operator=operator,
        certificate_id=state.active_certificate.id,
        tag_id=tag.id,
        physical_uid=tag.physical_uid,
        note="write-and-readback-confirmed",
    )
    _append_note(tag, record)
    return TagStepResult(tag_id=tag.id, physical_uid=tag.physical_uid, record=record)


def execute_lock(db: Session, public_code: str, *, operator: str) -> TagStepResult:
    """Record `programmed -> locked` in the database. Call it only after the
    physical lock was done and confirmed by the tool (decision G)."""
    state = _locked_state(db, public_code)
    blockers = lock_blockers(state)
    if blockers:
        raise PreconditionFailed(blockers)

    tag = db.get(NfcTag, state.programmed_tags[0].id)
    lock_nfc_tag(db, tag)
    record = operation_record(
        "lock",
        state.public_code,
        operator=operator,
        certificate_id=state.active_certificate.id,
        tag_id=tag.id,
        physical_uid=tag.physical_uid,
        note="physical-lock-confirmed",
    )
    _append_note(tag, record)
    return TagStepResult(tag_id=tag.id, physical_uid=tag.physical_uid, record=record)


def verify_token_resolves(db: Session, raw_token: str, public_code: str) -> bool:
    """Self-check through the real public resolve code path, in process (no
    HTTP, no network): the token must be `authentic` AND resolve to the piece
    the operator selected. It proves the database side only; the phone scan
    of the physical tag proves the rest (Cloudflare Pages, the API, the NDEF
    content)."""
    result = resolve_certificate(CertificateResolveRequest(token=raw_token), db)
    return isinstance(result, CertificateResolveAuthentic) and result.piece.public_code == public_code
