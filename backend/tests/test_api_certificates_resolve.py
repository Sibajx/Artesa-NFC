"""Focused tests for POST /api/v1/certificates/resolve (issue #66).

Uses the same db_session + get_db dependency-override pattern as
tests/test_api_pieces.py's rollback-scoped tests, plus the real
activate_certificate/hash_certificate_token helpers from
app.services.certificates so tests exercise the actual lifecycle instead of
hand-rolling a token hash.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app
from app.models.artisan import Artisan
from app.models.certificate import Certificate, CertificateStatus
from app.models.enums import PublicationStatus
from app.models.piece import Piece
from app.services.certificates import (
    activate_certificate,
    generate_certificate_token,
    hash_certificate_token,
    revoke_certificate,
)

client = TestClient(app)

UNAVAILABLE_BODY = {"authenticity": {"status": "unavailable"}}


def _override_get_db(session):
    def _override():
        yield session

    return _override


def _make_published_artisan(db_session, slug: str) -> Artisan:
    artisan = Artisan(
        slug=slug,
        full_name=f"Full Name {slug}",
        publication_status=PublicationStatus.published,
    )
    db_session.add(artisan)
    db_session.flush()
    return artisan


def _make_published_piece(db_session, artisan: Artisan, slug: str) -> Piece:
    piece = Piece(
        slug=slug,
        public_code=f"PC-{slug}",
        artisan_id=artisan.id,
        name=f"Piece {slug}",
        publication_status=PublicationStatus.published,
    )
    db_session.add(piece)
    db_session.flush()
    return piece


def _issue_active_certificate(db_session, piece: Piece, **overrides) -> tuple[Certificate, str]:
    cert = Certificate(piece_id=piece.id, status=CertificateStatus.draft)
    db_session.add(cert)
    db_session.flush()
    result = activate_certificate(db_session, cert)
    for key, value in overrides.items():
        setattr(result.certificate, key, value)
    db_session.flush()
    return result.certificate, result.raw_token


def _resolve(token: str):
    return client.post("/api/v1/certificates/resolve", json={"token": token})


class _Ctx:
    """Small helper to run a block of test setup against a real dependency-
    overridden session, mirroring test_api_pieces.py's try/finally pattern
    without repeating it at every call site."""

    def __init__(self, db_session):
        self.db_session = db_session

    def __enter__(self):
        app.dependency_overrides[get_db] = _override_get_db(self.db_session)
        return self.db_session

    def __exit__(self, *exc_info):
        app.dependency_overrides.pop(get_db, None)


# --- Authentic ---------------------------------------------------------------


def test_authentic_active_token_returns_200_and_authentic_status(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-authentic")
        piece = _make_published_piece(db_session, artisan, "piece-cert-authentic")
        _, raw_token = _issue_active_certificate(
            db_session, piece, authenticity_metadata={"notes": "Certified for testing"}
        )

        response = _resolve(raw_token)

        assert response.status_code == 200
        body = response.json()
        assert body["authenticity"]["status"] == "authentic"


def test_authentic_response_has_only_documented_top_level_fields(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-shape")
        piece = _make_published_piece(db_session, artisan, "piece-cert-shape")
        _, raw_token = _issue_active_certificate(db_session, piece)

        body = _resolve(raw_token).json()

        assert set(body.keys()) == {"authenticity", "piece", "artisan", "authenticity_metadata"}
        assert set(body["authenticity"].keys()) == {"status", "certificate_version", "issued_at"}


def test_authentic_piece_matches_public_piece_representation(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-piece-shape")
        piece = _make_published_piece(db_session, artisan, "piece-cert-piece-shape")
        _, raw_token = _issue_active_certificate(db_session, piece)

        body = _resolve(raw_token).json()

        assert body["piece"]["slug"] == piece.slug
        assert body["piece"]["public_code"] == piece.public_code
        assert set(body["piece"].keys()) == {
            "slug", "public_code", "name", "description", "history", "materials",
            "technique", "origin", "creation_year", "creation_date", "dimensions",
            "visual_theme", "availability_status", "artisan", "media",
        }


def test_authentic_artisan_matches_public_artisan_representation(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-artisan-shape")
        piece = _make_published_piece(db_session, artisan, "piece-cert-artisan-shape")
        _, raw_token = _issue_active_certificate(db_session, piece)

        body = _resolve(raw_token).json()

        assert body["artisan"]["slug"] == artisan.slug
        assert set(body["artisan"].keys()) == {
            "slug", "full_name", "artistic_name", "biography", "history", "location",
            "techniques", "languages", "public_contact", "media", "pieces",
        }
        assert body["artisan"]["pieces"][0]["slug"] == piece.slug


def test_authentic_authenticity_metadata_only_exposes_allowlisted_notes(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-metadata")
        piece = _make_published_piece(db_session, artisan, "piece-cert-metadata")
        _, raw_token = _issue_active_certificate(
            db_session,
            piece,
            authenticity_metadata={"notes": "Piloto Cuilápam", "internal_flag": "do-not-expose"},
        )

        response = _resolve(raw_token)
        body = response.json()

        assert body["authenticity_metadata"] == {"notes": "Piloto Cuilápam"}
        assert "internal_flag" not in response.text


def test_authentic_metadata_defaults_to_null_notes_when_absent(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-metadata-none")
        piece = _make_published_piece(db_session, artisan, "piece-cert-metadata-none")
        _, raw_token = _issue_active_certificate(db_session, piece)

        body = _resolve(raw_token).json()

        assert body["authenticity_metadata"] == {"notes": None}


def test_authentic_certificate_version_matches_stored_value(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-version")
        piece = _make_published_piece(db_session, artisan, "piece-cert-version")
        cert, raw_token = _issue_active_certificate(db_session, piece)

        body = _resolve(raw_token).json()

        assert body["authenticity"]["certificate_version"] == cert.version == 1


# --- Secret / internal-ID safety ---------------------------------------------


def test_authentic_response_never_contains_raw_token(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-secret")
        piece = _make_published_piece(db_session, artisan, "piece-cert-secret")
        _, raw_token = _issue_active_certificate(db_session, piece)

        response = _resolve(raw_token)

        assert raw_token not in response.text


def test_authentic_response_never_contains_token_hash(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-hash-leak")
        piece = _make_published_piece(db_session, artisan, "piece-cert-hash-leak")
        cert, raw_token = _issue_active_certificate(db_session, piece)

        response = _resolve(raw_token)

        assert cert.token_hash not in response.text
        assert "token_hash" not in response.text


def test_authentic_response_never_contains_internal_uuids(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-uuid-leak")
        piece = _make_published_piece(db_session, artisan, "piece-cert-uuid-leak")
        cert, raw_token = _issue_active_certificate(db_session, piece)

        raw = _resolve(raw_token).text

        assert str(cert.id) not in raw
        assert str(piece.id) not in raw
        assert str(artisan.id) not in raw
        assert '"id"' not in raw


# --- Unavailable: uniform result across every non-authentic cause -----------


def test_unknown_token_returns_unavailable():
    response = _resolve(generate_certificate_token())
    assert response.status_code == 200
    assert response.json() == UNAVAILABLE_BODY


def test_tampered_token_returns_unavailable(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-tampered")
        piece = _make_published_piece(db_session, artisan, "piece-cert-tampered")
        _, raw_token = _issue_active_certificate(db_session, piece)

        tampered = raw_token[:-1] + ("A" if raw_token[-1] != "A" else "B")
        response = _resolve(tampered)

        assert response.status_code == 200
        assert response.json() == UNAVAILABLE_BODY


def test_revoked_certificate_returns_unavailable(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-revoked")
        piece = _make_published_piece(db_session, artisan, "piece-cert-revoked")
        cert, raw_token = _issue_active_certificate(db_session, piece)
        revoke_certificate(db_session, cert)

        response = _resolve(raw_token)

        assert response.status_code == 200
        assert response.json() == UNAVAILABLE_BODY


def test_draft_certificate_token_is_never_resolvable(db_session):
    # A draft certificate has no token_hash at all (DATA_MODEL.md section
    # 2.3) - there is no raw token to submit for it in the first place.
    # This confirms that a piece whose only certificate row is `draft`
    # cannot be resolved by any token, including a freshly generated one
    # that happens to never have been issued to anyone.
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-draft")
        piece = _make_published_piece(db_session, artisan, "piece-cert-draft")
        db_session.add(Certificate(piece_id=piece.id, status=CertificateStatus.draft))
        db_session.flush()

        response = _resolve(generate_certificate_token())
        assert response.json() == UNAVAILABLE_BODY


def test_unpublished_piece_returns_unavailable(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-unpub-piece")
        piece = _make_published_piece(db_session, artisan, "piece-cert-unpub-piece")
        piece.publication_status = PublicationStatus.draft
        db_session.flush()
        _, raw_token = _issue_active_certificate(db_session, piece)

        response = _resolve(raw_token)

        assert response.status_code == 200
        assert response.json() == UNAVAILABLE_BODY


def test_unpublished_artisan_returns_unavailable(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-unpub-artisan")
        artisan.publication_status = PublicationStatus.draft
        db_session.flush()
        piece = _make_published_piece(db_session, artisan, "piece-cert-unpub-artisan")
        _, raw_token = _issue_active_certificate(db_session, piece)

        response = _resolve(raw_token)

        assert response.status_code == 200
        assert response.json() == UNAVAILABLE_BODY


def test_unavailable_bodies_are_identical_across_causes(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-cert-uniform")
        piece = _make_published_piece(db_session, artisan, "piece-cert-uniform")

        revoked_cert, revoked_token = _issue_active_certificate(db_session, piece)
        revoke_certificate(db_session, revoked_cert)

        unpub_artisan = _make_published_artisan(db_session, "artisan-cert-uniform-unpub")
        unpub_artisan.publication_status = PublicationStatus.draft
        db_session.flush()
        unpub_piece = _make_published_piece(db_session, unpub_artisan, "piece-cert-uniform-unpub")
        _, unpub_token = _issue_active_certificate(db_session, unpub_piece)

        unknown_body = _resolve(generate_certificate_token()).json()
        revoked_body = _resolve(revoked_token).json()
        unpublished_body = _resolve(unpub_token).json()
        malformed_body = _resolve("not-a-real-token-shape!!").json()

        assert unknown_body == revoked_body == unpublished_body == malformed_body == UNAVAILABLE_BODY


def test_all_unavailable_causes_share_http_200():
    responses = [
        _resolve(generate_certificate_token()),
        _resolve("short"),
        _resolve("x" * 43),
    ]
    assert all(r.status_code == 200 for r in responses)


# --- Token validation ---------------------------------------------------------


def test_missing_token_field_returns_422():
    response = client.post("/api/v1/certificates/resolve", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    fields = {d["field"] for d in body["error"]["details"]}
    assert "token" in fields


def test_wrong_type_token_returns_422():
    response = client.post("/api/v1/certificates/resolve", json={"token": 12345})
    assert response.status_code == 422


def test_too_long_token_returns_422_without_reaching_db():
    response = _resolve("A" * 257)
    assert response.status_code == 422


def test_token_at_max_length_boundary_is_accepted_as_unavailable():
    response = _resolve("A" * 256)
    assert response.status_code == 200
    assert response.json() == UNAVAILABLE_BODY


def test_wrong_length_but_plausible_token_is_unavailable_not_422():
    response = _resolve("A" * 40)
    assert response.status_code == 200
    assert response.json() == UNAVAILABLE_BODY


def test_invalid_alphabet_token_is_unavailable_not_422():
    response = _resolve("!" * 43)
    assert response.status_code == 200
    assert response.json() == UNAVAILABLE_BODY


def test_empty_string_token_is_unavailable_not_422():
    response = _resolve("")
    assert response.status_code == 200
    assert response.json() == UNAVAILABLE_BODY


# --- 422 must never echo the submitted token ---------------------------------


def test_422_for_wrong_type_does_not_echo_submitted_value():
    secret_marker = "super-secret-token-value-should-not-echo"
    response = client.post("/api/v1/certificates/resolve", json={"token": [secret_marker]})
    assert response.status_code == 422
    assert secret_marker not in response.text


def test_422_for_too_long_token_does_not_echo_submitted_value():
    long_token = "B" * 300
    response = _resolve(long_token)
    assert response.status_code == 422
    assert long_token not in response.text


# --- Public API regression: no certificate/NFC leakage elsewhere -------------


def test_pieces_list_endpoint_unaffected_by_certificates_endpoint():
    response = client.get("/api/v1/pieces")
    raw = response.text
    for forbidden in ("has_certificate", "certificate_id", "certificate_status", "has_nfc", "nfc_tag", "token_hash"):
        assert forbidden not in raw


def test_artisans_list_endpoint_unaffected_by_certificates_endpoint():
    response = client.get("/api/v1/artisans")
    raw = response.text
    for forbidden in ("has_certificate", "certificate_id", "certificate_status", "has_nfc", "nfc_tag", "token_hash"):
        assert forbidden not in raw
