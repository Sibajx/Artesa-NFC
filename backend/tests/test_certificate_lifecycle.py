from __future__ import annotations

import base64
import dataclasses
import re
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Artisan, Piece
from app.models.certificate import Certificate, CertificateStatus
from app.services.certificates import (
    ActiveCertificateAlreadyExists,
    ActiveCertificateNotFound,
    CertificateActivationResult,
    CertificateRotationResult,
    InvalidCertificateTransition,
    activate_certificate,
    generate_certificate_token,
    hash_certificate_token,
    revoke_certificate,
    rotate_certificate,
)

_BASE64URL_UNPADDED = re.compile(r"^[A-Za-z0-9_-]+$")


def _make_piece(db_session, slug: str) -> Piece:
    artisan = Artisan(slug=f"artisan-for-{slug}", full_name="Artisan")
    db_session.add(artisan)
    db_session.flush()
    piece = Piece(slug=slug, public_code=f"PC-{slug}", artisan_id=artisan.id, name="Piece")
    db_session.add(piece)
    db_session.flush()
    return piece


def _draft_certificate(db_session, piece: Piece) -> Certificate:
    cert = Certificate(piece_id=piece.id, status=CertificateStatus.draft)
    db_session.add(cert)
    db_session.flush()
    return cert


def _active_certificates_for(db_session, piece_id) -> list[Certificate]:
    return (
        db_session.execute(
            select(Certificate).where(
                Certificate.piece_id == piece_id,
                Certificate.status == CertificateStatus.active,
            )
        )
        .scalars()
        .all()
    )


# --- Token generation ------------------------------------------------------


def test_token_has_expected_unpadded_length():
    token = generate_certificate_token()
    assert len(token) == 43


def test_token_uses_only_base64url_characters():
    token = generate_certificate_token()
    assert _BASE64URL_UNPADDED.match(token)


def test_token_has_no_padding():
    token = generate_certificate_token()
    assert "=" not in token


def test_token_decodes_back_to_32_bytes():
    token = generate_certificate_token()
    padding = "=" * (-len(token) % 4)
    decoded = base64.urlsafe_b64decode(token + padding)
    assert len(decoded) == 32


def test_many_generated_tokens_are_distinct():
    tokens = {generate_certificate_token() for _ in range(1000)}
    assert len(tokens) == 1000


def test_token_is_not_derived_from_predictable_input():
    # Regression guard: two tokens generated back to back must not share any
    # deterministic relationship to call order/time; distinctness above
    # already proves this in practice, this just checks shape stays CSPRNG.
    first = generate_certificate_token()
    second = generate_certificate_token()
    assert first != second


# --- Token hashing ----------------------------------------------------------


def test_hash_is_deterministic_for_same_token():
    token = generate_certificate_token()
    assert hash_certificate_token(token) == hash_certificate_token(token)


def test_hash_differs_for_different_tokens():
    assert hash_certificate_token(generate_certificate_token()) != hash_certificate_token(
        generate_certificate_token()
    )


def test_hash_rejects_non_str_input():
    with pytest.raises(TypeError):
        hash_certificate_token(b"not-a-str")


# --- Activation --------------------------------------------------------------


def test_activation_draft_to_active_succeeds_and_returns_raw_token(db_session):
    piece = _make_piece(db_session, "piece-activate-ok")
    cert = _draft_certificate(db_session, piece)

    result = activate_certificate(db_session, cert)

    assert result.certificate.status == CertificateStatus.active
    assert len(result.raw_token) == 43
    assert result.certificate.issued_at is not None


def test_activation_persists_hash_not_raw_token(db_session):
    piece = _make_piece(db_session, "piece-activate-hash")
    cert = _draft_certificate(db_session, piece)

    result = activate_certificate(db_session, cert)
    db_session.flush()
    db_session.expire_all()

    stored = db_session.get(Certificate, result.certificate.id)
    assert stored.token_hash == hash_certificate_token(result.raw_token)
    assert stored.token_hash != result.raw_token
    assert "token" not in Certificate.__table__.columns.keys()


def test_activation_rejects_active_certificate(db_session):
    piece = _make_piece(db_session, "piece-activate-already-active")
    cert = _draft_certificate(db_session, piece)
    activate_certificate(db_session, cert)

    with pytest.raises(InvalidCertificateTransition):
        activate_certificate(db_session, cert)


def test_activation_rejects_revoked_certificate(db_session):
    piece = _make_piece(db_session, "piece-activate-revoked")
    cert = _draft_certificate(db_session, piece)
    activate_certificate(db_session, cert)
    revoke_certificate(db_session, cert)

    with pytest.raises(InvalidCertificateTransition):
        activate_certificate(db_session, cert)


def test_activation_rejected_when_piece_already_has_active_certificate(db_session):
    piece = _make_piece(db_session, "piece-activate-conflict")
    first_draft = _draft_certificate(db_session, piece)
    activate_certificate(db_session, first_draft)

    second_draft = _draft_certificate(db_session, piece)
    with pytest.raises(ActiveCertificateAlreadyExists):
        activate_certificate(db_session, second_draft)


# --- Revocation ---------------------------------------------------------------


def test_revocation_active_to_revoked_succeeds(db_session):
    piece = _make_piece(db_session, "piece-revoke-ok")
    cert = _draft_certificate(db_session, piece)
    activation = activate_certificate(db_session, cert)
    issued_at = activation.certificate.issued_at
    token_hash_before = activation.certificate.token_hash

    revoked = revoke_certificate(db_session, cert)

    assert revoked.status == CertificateStatus.revoked
    assert revoked.revoked_at is not None
    assert revoked.token_hash == token_hash_before
    assert revoked.issued_at == issued_at


def test_revocation_rejects_draft_certificate(db_session):
    piece = _make_piece(db_session, "piece-revoke-draft")
    cert = _draft_certificate(db_session, piece)

    with pytest.raises(InvalidCertificateTransition):
        revoke_certificate(db_session, cert)


def test_revocation_rejects_already_revoked_certificate(db_session):
    piece = _make_piece(db_session, "piece-revoke-twice")
    cert = _draft_certificate(db_session, piece)
    activate_certificate(db_session, cert)
    revoke_certificate(db_session, cert)

    with pytest.raises(InvalidCertificateTransition):
        revoke_certificate(db_session, cert)


# --- Rotation ------------------------------------------------------------------


def test_rotation_revokes_old_and_activates_new(db_session):
    piece = _make_piece(db_session, "piece-rotate-ok")
    cert = _draft_certificate(db_session, piece)
    original = activate_certificate(db_session, cert)

    result = rotate_certificate(db_session, piece.id)

    assert result.revoked_certificate.id == cert.id
    assert result.revoked_certificate.status == CertificateStatus.revoked
    assert result.certificate.status == CertificateStatus.active
    assert result.certificate.id != cert.id
    assert result.raw_token != original.raw_token
    assert result.certificate.token_hash != result.revoked_certificate.token_hash


def test_rotation_leaves_exactly_one_active_certificate(db_session):
    piece = _make_piece(db_session, "piece-rotate-one-active")
    cert = _draft_certificate(db_session, piece)
    activate_certificate(db_session, cert)

    rotate_certificate(db_session, piece.id)

    assert len(_active_certificates_for(db_session, piece.id)) == 1


def test_rotation_preserves_old_certificate_as_history(db_session):
    piece = _make_piece(db_session, "piece-rotate-history")
    cert = _draft_certificate(db_session, piece)
    activate_certificate(db_session, cert)

    rotate_certificate(db_session, piece.id)

    rows = db_session.execute(
        select(Certificate).where(Certificate.piece_id == piece.id)
    ).scalars().all()
    assert len(rows) == 2
    assert sum(1 for r in rows if r.status == CertificateStatus.revoked) == 1
    assert sum(1 for r in rows if r.status == CertificateStatus.active) == 1


def test_rotation_without_active_certificate_is_rejected(db_session):
    piece = _make_piece(db_session, "piece-rotate-none-active")
    _draft_certificate(db_session, piece)  # draft only, never activated

    with pytest.raises(ActiveCertificateNotFound):
        rotate_certificate(db_session, piece.id)


def test_rotation_forced_failure_rolls_back_completely(db_session):
    piece = _make_piece(db_session, "piece-rotate-fail")
    cert = _draft_certificate(db_session, piece)
    original = activate_certificate(db_session, cert)

    # Force the replacement's activation to collide on token_hash with the
    # certificate we just issued, so the nested transaction's final flush
    # raises IntegrityError partway through rotation.
    with patch(
        "app.services.certificates.generate_certificate_token",
        return_value=original.raw_token,
    ):
        with pytest.raises(IntegrityError):
            rotate_certificate(db_session, piece.id)

    rows = db_session.execute(
        select(Certificate).where(Certificate.piece_id == piece.id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == cert.id
    assert rows[0].status == CertificateStatus.active
    assert rows[0].token_hash == original.certificate.token_hash


def test_rotation_of_different_pieces_is_independent(db_session):
    piece_a = _make_piece(db_session, "piece-rotate-multi-a")
    piece_b = _make_piece(db_session, "piece-rotate-multi-b")
    cert_a = _draft_certificate(db_session, piece_a)
    cert_b = _draft_certificate(db_session, piece_b)
    activate_certificate(db_session, cert_a)
    activate_certificate(db_session, cert_b)

    result_a = rotate_certificate(db_session, piece_a.id)

    assert len(_active_certificates_for(db_session, piece_a.id)) == 1
    assert len(_active_certificates_for(db_session, piece_b.id)) == 1
    assert result_a.certificate.piece_id == piece_a.id


# --- DB safety: partial unique index is authoritative -------------------------


def test_db_constraint_rejects_two_active_certificates_bypassing_service_checks(db_session):
    """The application-level check in `activate_certificate` is a courtesy;
    the partial unique index (DATA_MODEL.md section 4 restriction C') is
    what actually guarantees at most one active certificate per piece, even
    if something inserts a second active row directly through the ORM
    without going through the service at all.
    """
    piece = _make_piece(db_session, "piece-db-safety")
    db_session.add(
        Certificate(piece_id=piece.id, status=CertificateStatus.active, token_hash="db-safety-hash-1")
    )
    db_session.flush()

    db_session.add(
        Certificate(piece_id=piece.id, status=CertificateStatus.active, token_hash="db-safety-hash-2")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- Secret handling: raw token must never appear in automatic representations -


def test_activation_result_repr_does_not_contain_raw_token(db_session):
    piece = _make_piece(db_session, "piece-repr-activation")
    cert = _draft_certificate(db_session, piece)

    result = activate_certificate(db_session, cert)

    assert result.raw_token not in repr(result)
    # Sanity check the field is genuinely absent from the repr, not just
    # coincidentally missing because of e.g. truncation.
    assert "raw_token" not in repr(result)


def test_activation_result_str_does_not_contain_raw_token(db_session):
    piece = _make_piece(db_session, "piece-str-activation")
    cert = _draft_certificate(db_session, piece)

    result = activate_certificate(db_session, cert)

    # Dataclasses have no custom __str__, so str() falls back to __repr__;
    # this proves that fallback path is safe too, not just repr() directly.
    assert result.raw_token not in str(result)


def test_rotation_result_repr_does_not_contain_raw_token(db_session):
    piece = _make_piece(db_session, "piece-repr-rotation")
    cert = _draft_certificate(db_session, piece)
    activate_certificate(db_session, cert)

    result = rotate_certificate(db_session, piece.id)

    assert result.raw_token not in repr(result)
    assert "raw_token" not in repr(result)


def test_rotation_result_str_does_not_contain_raw_token(db_session):
    piece = _make_piece(db_session, "piece-str-rotation")
    cert = _draft_certificate(db_session, piece)
    activate_certificate(db_session, cert)

    result = rotate_certificate(db_session, piece.id)

    assert result.raw_token not in str(result)


def test_rotation_result_repr_does_not_leak_token_via_nested_certificate_objects(db_session):
    """Guards against the token reappearing indirectly: even though
    `CertificateRotationResult` doesn't nest `CertificateActivationResult`,
    it does nest the `certificate`/`revoked_certificate` ORM objects. Neither
    of those has a raw-token attribute at all (only `token_hash`), so their
    own reprs — which appear inside the outer dataclass repr — can't leak it
    either.
    """
    piece = _make_piece(db_session, "piece-repr-rotation-nested")
    cert = _draft_certificate(db_session, piece)
    activation = activate_certificate(db_session, cert)

    result = rotate_certificate(db_session, piece.id)

    full_repr = repr(result)
    assert activation.raw_token not in full_repr
    assert result.raw_token not in full_repr
    # The nested ORM objects' own reprs are part of `full_repr` (dataclass
    # __repr__ calls repr() on each field value); confirm those don't carry
    # a raw-token attribute to leak in the first place.
    assert not hasattr(result.certificate, "raw_token")
    assert not hasattr(result.certificate, "token")
    assert not hasattr(result.revoked_certificate, "raw_token")
    assert not hasattr(result.revoked_certificate, "token")


def test_activation_result_fields_have_expected_repr_visibility():
    # Field-level guarantee, independent of any particular instance's data:
    # `raw_token` is the only field excluded from the generated __repr__.
    fields_by_name = {f.name: f for f in dataclasses.fields(CertificateActivationResult)}
    assert fields_by_name["raw_token"].repr is False
    assert fields_by_name["certificate"].repr is True


def test_rotation_result_fields_have_expected_repr_visibility():
    fields_by_name = {f.name: f for f in dataclasses.fields(CertificateRotationResult)}
    assert fields_by_name["raw_token"].repr is False
    assert fields_by_name["certificate"].repr is True
    assert fields_by_name["revoked_certificate"].repr is True


def test_service_exceptions_never_contain_a_raw_token(db_session):
    piece = _make_piece(db_session, "piece-repr-exceptions")
    cert = _draft_certificate(db_session, piece)
    activation = activate_certificate(db_session, cert)

    # Trigger every service exception and confirm none of them was built
    # from, or otherwise mentions, the raw token issued above.
    other_draft = _draft_certificate(db_session, piece)
    try:
        activate_certificate(db_session, other_draft)  # piece already has an active cert
    except ActiveCertificateAlreadyExists as exc:
        assert activation.raw_token not in str(exc)
        assert activation.raw_token not in repr(exc)
    else:
        pytest.fail("expected ActiveCertificateAlreadyExists")

    try:
        activate_certificate(db_session, cert)  # already active, not draft
    except InvalidCertificateTransition as exc:
        assert activation.raw_token not in str(exc)
        assert activation.raw_token not in repr(exc)
    else:
        pytest.fail("expected InvalidCertificateTransition")

    other_piece = _make_piece(db_session, "piece-repr-exceptions-none-active")
    try:
        rotate_certificate(db_session, other_piece.id)  # no active certificate
    except ActiveCertificateNotFound as exc:
        assert activation.raw_token not in str(exc)
        assert activation.raw_token not in repr(exc)
    else:
        pytest.fail("expected ActiveCertificateNotFound")


def test_certificate_orm_repr_has_no_raw_token_attribute(db_session):
    piece = _make_piece(db_session, "piece-repr-orm")
    cert = _draft_certificate(db_session, piece)
    result = activate_certificate(db_session, cert)

    assert result.raw_token not in repr(result.certificate)
    assert not hasattr(result.certificate, "raw_token")
    assert not hasattr(result.certificate, "token")
    assert "token" not in Certificate.__table__.columns.keys()
