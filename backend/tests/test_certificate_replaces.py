"""PEND-032: a rotation links the new certificate to the one it replaces."""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Artisan, Piece
from app.models.certificate import Certificate, CertificateStatus
from app.services.certificates import issue_certificate, revoke_certificate, rotate_certificate


def _piece(db_session, slug: str) -> Piece:
    artisan = Artisan(slug=f"artisan-for-{slug}", full_name="Artisan")
    db_session.add(artisan)
    db_session.flush()
    piece = Piece(slug=slug, public_code=f"PC-{slug}", artisan_id=artisan.id, name="Piece")
    db_session.add(piece)
    db_session.flush()
    return piece


def test_first_certificate_replaces_nothing(db_session):
    piece = _piece(db_session, "replaces-first")
    first = issue_certificate(db_session, piece.id).certificate
    assert first.replaces_certificate_id is None


def test_rotation_points_the_new_certificate_at_the_old_one(db_session):
    piece = _piece(db_session, "replaces-rotation")
    first = issue_certificate(db_session, piece.id).certificate
    rotation = rotate_certificate(db_session, piece.id, reason="lost")
    assert rotation.certificate.replaces_certificate_id == first.id
    assert rotation.revoked_certificate.id == first.id

    third = rotate_certificate(db_session, piece.id).certificate
    assert third.replaces_certificate_id == rotation.certificate.id


def test_issuing_after_a_plain_revocation_leaves_the_link_empty(db_session):
    piece = _piece(db_session, "replaces-revoke")
    first = issue_certificate(db_session, piece.id).certificate
    revoke_certificate(db_session, first, reason="other")
    second = issue_certificate(db_session, piece.id).certificate
    assert second.replaces_certificate_id is None


def test_a_certificate_cannot_replace_itself(db_session):
    piece = _piece(db_session, "replaces-self")
    cert = Certificate(piece_id=piece.id, status=CertificateStatus.draft)
    db_session.add(cert)
    db_session.flush()
    cert.replaces_certificate_id = cert.id
    with pytest.raises(IntegrityError):
        db_session.flush()
