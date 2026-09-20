"""`issue_certificate` and the optional `reason` of revoke/rotate (issue N-09).

Additive service changes: the existing activation/revocation/rotation
semantics are covered by test_certificate_lifecycle.py and must not change.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models import Artisan, Piece
from app.models.certificate import Certificate, CertificateStatus
from app.services.certificates import (
    ActiveCertificateAlreadyExists,
    CertificateLifecycleIntegrityError,
    hash_certificate_token,
    is_syntactically_plausible_token,
    issue_certificate,
    revoke_certificate,
    rotate_certificate,
)


def _piece(db_session, slug: str) -> Piece:
    artisan = Artisan(slug=f"artisan-for-{slug}", full_name="Artisan")
    db_session.add(artisan)
    db_session.flush()
    piece = Piece(slug=slug, public_code=f"PC-{slug}", artisan_id=artisan.id, name="Piece")
    db_session.add(piece)
    db_session.flush()
    return piece


def _count(db_session, piece_id) -> int:
    return db_session.execute(
        select(func.count()).select_from(Certificate).where(Certificate.piece_id == piece_id)
    ).scalar_one()


def test_issue_creates_an_active_certificate_with_a_single_use_token(db_session):
    piece = _piece(db_session, "issue-1")

    result = issue_certificate(db_session, piece.id)

    assert result.certificate.status == CertificateStatus.active
    assert result.certificate.issued_at is not None
    assert result.certificate.revoked_at is None
    assert is_syntactically_plausible_token(result.raw_token)
    assert result.certificate.token_hash == hash_certificate_token(result.raw_token)
    assert result.raw_token not in repr(result)
    assert result.raw_token not in str(result)


def test_issue_never_persists_the_raw_token(db_session):
    piece = _piece(db_session, "issue-2")
    result = issue_certificate(db_session, piece.id)
    db_session.flush()
    row = db_session.execute(select(Certificate)).scalars().first()
    values = [str(getattr(row, c.key)) for c in Certificate.__table__.columns]
    assert all(result.raw_token not in value for value in values)


def test_issue_rejects_a_piece_that_already_has_an_active_certificate_and_leaves_no_orphan(db_session):
    piece = _piece(db_session, "issue-3")
    issue_certificate(db_session, piece.id)
    assert _count(db_session, piece.id) == 1

    with pytest.raises(ActiveCertificateAlreadyExists):
        issue_certificate(db_session, piece.id)

    # The draft the failed attempt inserted was rolled back with its SAVEPOINT.
    assert _count(db_session, piece.id) == 1


def test_issue_after_a_revocation_keeps_the_history(db_session):
    piece = _piece(db_session, "issue-4")
    first = issue_certificate(db_session, piece.id)
    revoke_certificate(db_session, first.certificate)

    second = issue_certificate(db_session, piece.id)

    assert second.certificate.id != first.certificate.id
    assert second.raw_token != first.raw_token
    assert second.certificate.token_hash != first.certificate.token_hash
    assert first.certificate.status == CertificateStatus.revoked
    assert _count(db_session, piece.id) == 2


def test_issue_for_an_unknown_piece_fails_without_leaking(db_session):
    with pytest.raises(CertificateLifecycleIntegrityError) as info:
        issue_certificate(db_session, uuid.uuid4())
    assert "token" not in str(info.value).lower().replace("token_hash", "")


def test_revoke_stores_the_reason_when_given(db_session):
    piece = _piece(db_session, "reason-1")
    cert = issue_certificate(db_session, piece.id).certificate

    revoke_certificate(db_session, cert, reason="lost")

    assert cert.status == CertificateStatus.revoked
    assert cert.revocation_reason == "lost"


def test_revoke_without_a_reason_leaves_the_column_untouched(db_session):
    piece = _piece(db_session, "reason-2")
    cert = issue_certificate(db_session, piece.id).certificate

    revoke_certificate(db_session, cert)

    assert cert.status == CertificateStatus.revoked
    assert cert.revocation_reason is None


def test_rotate_records_the_reason_on_the_revoked_certificate_only(db_session):
    piece = _piece(db_session, "reason-3")
    issue_certificate(db_session, piece.id)

    rotation = rotate_certificate(db_session, piece.id, reason="compromised")

    assert rotation.revoked_certificate.revocation_reason == "compromised"
    assert rotation.certificate.revocation_reason is None
    assert rotation.certificate.status == CertificateStatus.active


def test_rotate_without_a_reason_behaves_as_before(db_session):
    piece = _piece(db_session, "reason-4")
    issue_certificate(db_session, piece.id)

    rotation = rotate_certificate(db_session, piece.id)

    assert rotation.revoked_certificate.revocation_reason is None
    assert rotation.revoked_certificate.status == CertificateStatus.revoked
