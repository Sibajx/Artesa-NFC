"""Hardening regression tests for POST /api/v1/certificates/resolve (issue #74).

Covers, against real PostgreSQL (no SQLite substitution):
  - secret safety: the submitted token / token_hash / physical_uid never
    reach a response body or captured log output, and the SQLAlchemy engine
    hides bound parameters;
  - unavailable convergence across every non-authentic cause;
  - Cache-Control: no-store scoped to the resolve path only;
  - CORS policy unchanged by the hardening work;
  - static checks on the Nginx example (syntax/behavior of the real
    Nginx is verified separately with `nginx -t`; nothing here pretends to
    test rate limiting from Python - enforcement lives in Nginx).

Reuses the fixtures/helpers of test_api_certificates_resolve.py so the
setup of published/revoked/unpublished data stays defined in one place.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db.base import engine
from app.main import app
from app.models.certificate import Certificate, CertificateStatus
from app.models.enums import PublicationStatus
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.services.certificates import generate_certificate_token, hash_certificate_token, revoke_certificate
from tests.test_api_certificates_resolve import (
    UNAVAILABLE_BODY,
    _Ctx,
    _issue_active_certificate,
    _make_published_artisan,
    _make_published_piece,
)

client = TestClient(app)

RESOLVE_PATH = "/api/v1/certificates/resolve"
ALLOWED_ORIGIN = get_settings().cors_allowed_origins_list[0]
NGINX_EXAMPLE = Path(__file__).resolve().parents[1] / "nginx" / "artesanfc-api.conf.example"


def _resolve(token):
    return client.post(RESOLVE_PATH, json={"token": token})


# --- Secret safety: responses ------------------------------------------------


def test_unknown_token_response_does_not_echo_token():
    token = generate_certificate_token()
    response = _resolve(token)
    assert response.status_code == 200
    assert token not in response.text
    assert hash_certificate_token(token) not in response.text


def test_oversized_token_error_does_not_echo_token():
    token = "S3CRET" + "A" * 300
    response = _resolve(token)
    assert response.status_code == 422
    assert "S3CRET" not in response.text


def test_malformed_json_body_error_does_not_echo_body():
    response = client.post(
        RESOLVE_PATH,
        content='{"token": "S3CRET-MALFORMED", ',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert "S3CRET-MALFORMED" not in response.text


def test_missing_token_error_does_not_echo_other_body_content():
    response = client.post(RESOLVE_PATH, json={"not_token": "S3CRET-OTHER-FIELD"})
    assert response.status_code == 422
    assert "S3CRET-OTHER-FIELD" not in response.text


# --- Secret safety: logs and SQLAlchemy ---------------------------------------


def test_engine_hides_bound_parameters():
    assert engine.hide_parameters is True


def test_db_error_text_does_not_contain_bound_parameters():
    marker = "S3CRET-BOUND-PARAM"
    with engine.connect() as connection:
        with pytest.raises(DBAPIError) as exc_info:
            connection.execute(
                text("SELECT no_such_column FROM certificate WHERE token_hash = :h"),
                {"h": marker},
            )
    assert marker not in str(exc_info.value)


def test_logs_never_contain_token_or_token_hash_even_with_sql_logging_on(caplog):
    # Uses the real get_db (its own pooled connection, opened after the
    # logger level is raised) so SQLAlchemy actually emits its statement log
    # lines - the case where bound token_hash values would leak without
    # hide_parameters.
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    token = generate_certificate_token()
    token_hash = hash_certificate_token(token)

    response = _resolve(token)

    assert response.status_code == 200
    assert "certificate" in caplog.text  # the SELECT was really logged...
    assert "hidden due to hide_parameters" in caplog.text  # ...with params hidden
    assert token not in caplog.text
    assert token_hash not in caplog.text


def test_logs_and_response_never_contain_physical_uid(db_session, caplog):
    caplog.set_level(logging.DEBUG)
    physical_uid = "04:A1:B2:C3:D4:E5:F6"
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-hardening-uid")
        piece = _make_published_piece(db_session, artisan, "piece-hardening-uid")
        db_session.add(
            NfcTag(
                piece_id=piece.id,
                chip_model="NTAG213",
                status=NfcTagStatus.programmed,
                physical_uid=physical_uid,
            )
        )
        cert, raw_token = _issue_active_certificate(db_session, piece)

        response = _resolve(raw_token)

    assert response.status_code == 200
    assert response.json()["authenticity"]["status"] == "authentic"
    for secret in (physical_uid, raw_token, cert.token_hash):
        assert secret not in response.text
        assert secret not in caplog.text


# --- Unavailable convergence ---------------------------------------------------


def test_every_unavailable_cause_yields_identical_response(db_session):
    with _Ctx(db_session):
        # revoked
        artisan = _make_published_artisan(db_session, "artisan-hardening-revoked")
        piece = _make_published_piece(db_session, artisan, "piece-hardening-revoked")
        cert, revoked_token = _issue_active_certificate(db_session, piece)
        revoke_certificate(db_session, cert)

        # draft: a draft certificate carries no token (DATA_MODEL.md section
        # 2.3), so any token is "valid but unavailable" for a piece that only
        # has a draft certificate.
        draft_artisan = _make_published_artisan(db_session, "artisan-hardening-draft")
        draft_piece = _make_published_piece(db_session, draft_artisan, "piece-hardening-draft")
        db_session.add(Certificate(piece_id=draft_piece.id, status=CertificateStatus.draft))

        # unpublished piece
        up_artisan = _make_published_artisan(db_session, "artisan-hardening-unpub-piece")
        up_piece = _make_published_piece(db_session, up_artisan, "piece-hardening-unpub-piece")
        up_piece.publication_status = PublicationStatus.draft
        db_session.flush()
        _, unpub_piece_token = _issue_active_certificate(db_session, up_piece)

        # unpublished artisan
        ua_artisan = _make_published_artisan(db_session, "artisan-hardening-unpub-artisan")
        ua_artisan.publication_status = PublicationStatus.draft
        db_session.flush()
        ua_piece = _make_published_piece(db_session, ua_artisan, "piece-hardening-unpub-artisan")
        _, unpub_artisan_token = _issue_active_certificate(db_session, ua_piece)

        responses = {
            "unknown": _resolve(generate_certificate_token()),
            "revoked": _resolve(revoked_token),
            "draft": _resolve(generate_certificate_token()),
            "unpublished_piece": _resolve(unpub_piece_token),
            "unpublished_artisan": _resolve(unpub_artisan_token),
        }

    reference = responses["unknown"]
    for cause, response in responses.items():
        assert response.status_code == 200, cause
        assert response.json() == UNAVAILABLE_BODY, cause
        # Byte-identical body and headers (apart from Date, which the server
        # may stamp differently between calls).
        assert response.content == reference.content, cause
        assert _headers_without_date(response) == _headers_without_date(reference), cause


def _headers_without_date(response) -> dict[str, str]:
    return {k: v for k, v in response.headers.items() if k != "date"}


# --- Cache-Control: no-store (scoped to resolve) ---------------------------------


def test_authentic_response_is_no_store(db_session):
    with _Ctx(db_session):
        artisan = _make_published_artisan(db_session, "artisan-hardening-cache")
        piece = _make_published_piece(db_session, artisan, "piece-hardening-cache")
        _, raw_token = _issue_active_certificate(db_session, piece)

        response = _resolve(raw_token)

    assert response.json()["authenticity"]["status"] == "authentic"
    assert response.headers["cache-control"] == "no-store"


def test_unavailable_response_is_no_store():
    response = _resolve(generate_certificate_token())
    assert response.json() == UNAVAILABLE_BODY
    assert response.headers["cache-control"] == "no-store"


def test_validation_error_response_is_no_store():
    response = _resolve("A" * 257)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


def test_missing_body_validation_error_is_no_store():
    response = client.post(RESOLVE_PATH, json={})
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


def test_method_not_allowed_on_resolve_is_no_store():
    response = client.get(RESOLVE_PATH)
    assert response.status_code == 405
    assert response.headers["cache-control"] == "no-store"


def test_trailing_slash_redirect_on_resolve_is_no_store():
    response = client.post(RESOLVE_PATH + "/", json={"token": "x"}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["cache-control"] == "no-store"


def test_no_store_response_keeps_cors_headers_for_allowed_origin():
    response = client.post(RESOLVE_PATH, headers={"Origin": ALLOWED_ORIGIN}, json={"token": "x"})
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/artisans",
        "/api/v1/pieces",
        "/api/v1/artisans/does-not-exist",
        "/api/v1/pieces/does-not-exist",
        "/api/v1/certificates/other",
    ],
)
# /health is deliberately not listed: it sets its own `no-store`
# (tests/test_health.py), not this resolve-scoped middleware's.
def test_other_paths_do_not_get_no_store(path):
    response = client.get(path)
    assert "cache-control" not in response.headers


# --- CORS unchanged ----------------------------------------------------------------


def test_resolve_post_preflight_still_works():
    response = client.options(
        RESOLVE_PATH,
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "access-control-allow-credentials" not in response.headers


def test_cors_middleware_config_is_unchanged():
    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert cors.kwargs["allow_origins"] == get_settings().cors_allowed_origins_list
    assert "*" not in cors.kwargs["allow_origins"]
    assert cors.kwargs["allow_methods"] == ["GET", "POST"]
    assert cors.kwargs["allow_credentials"] is False
    assert "allow_origin_regex" not in cors.kwargs


# --- Nginx example: static checks only ------------------------------------------------
# These guard the shape of the shipped example. They say nothing about live
# enforcement, which must be verified on the real deployment.


def _active_nginx_lines() -> list[str]:
    """Non-comment, non-blank lines of the example config."""
    lines = NGINX_EXAMPLE.read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def test_nginx_example_defines_approved_rate_limits():
    active = "\n".join(_active_nginx_lines())
    assert re.search(r"limit_req_zone \$binary_remote_addr zone=artesanfc_resolve:\S+\s+rate=30r/m;", active)
    assert re.search(r"limit_req_zone \$binary_remote_addr zone=artesanfc_public_get:\S+\s+rate=120r/m;", active)
    assert "limit_req_status 429;" in active
    assert re.search(r"limit_req zone=artesanfc_resolve burst=\d+ nodelay;", active)


def test_nginx_example_limits_resolve_body_and_sets_no_store():
    active = "\n".join(_active_nginx_lines())
    assert "client_max_body_size 1k;" in active
    assert 'add_header Cache-Control "no-store" always;' in active


def test_nginx_example_does_not_enable_cloudflare_real_ip_by_default():
    active = "\n".join(_active_nginx_lines())
    assert "real_ip_header" not in active
    assert "set_real_ip_from" not in active
    # ...but the optional section is documented (as comments).
    assert "real_ip_header CF-Connecting-IP;" in NGINX_EXAMPLE.read_text()


def test_nginx_example_never_trusts_client_forwarded_for_or_logs_bodies():
    active = "\n".join(_active_nginx_lines())
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in active
    assert "$http_x_forwarded_for" not in active
    assert "$proxy_add_x_forwarded_for" not in active
    log_format = next(ln for ln in _active_nginx_lines() if ln.startswith("log_format"))
    for forbidden in ("$request_body", "$request_uri", "$args", "$query_string", "$request "):
        assert forbidden not in log_format
