"""Audit finding F-10 (lifecycle scope): PostgreSQL's DETAIL for a constraint
failure embeds row values, which for `certificate` includes `token_hash`
(docs/SECURITY.md sections 3, 12.2, 13). Lifecycle service errors must never
carry, or chain to, that text.

Scope note: this proves the *lifecycle service boundary*. A database error
raised outside these services (a caller's own commit/flush, any other code
path) still reaches the process log through the global 500 path; that is a
separate follow-up (global exception/log sanitization) and is not asserted
here.

Every "absent" assertion is paired with a premise check that the raw driver
error really does contain the canary, otherwise the tests could pass for
the wrong reason.
"""
from __future__ import annotations

import ast
import logging
import traceback
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.models import Artisan, Piece
from app.models.certificate import Certificate, CertificateStatus
from app.services import certificates as certificate_service
from app.services import lifecycle as lifecycle_service
from app.services import nfc_tags as nfc_service
from app.services.certificates import (
    CertificateLifecycleIntegrityError,
    CertificateServiceError,
    activate_certificate,
    hash_certificate_token,
    revoke_certificate,
    rotate_certificate,
)
from app.services.lifecycle import (
    LifecycleConflict,
    LifecycleError,
    LifecycleIntegrityError,
    run_in_savepoint,
)

CANARY_TOKEN = "CanaryTokenAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # 43 chars, never a real token
CANARY_HASH = hash_certificate_token(CANARY_TOKEN)

_FORBIDDEN_DB_TEXT = ("DETAIL", "Key (", "Failing row", "already exists", "postgresql://", "psycopg")


def _piece(db, slug: str) -> Piece:
    artisan = Artisan(slug=f"artisan-{slug}", full_name="Artisan")
    db.add(artisan)
    db.flush()
    piece = Piece(slug=slug, public_code=f"PC-{slug}", artisan_id=artisan.id, name="Piece")
    db.add(piece)
    db.flush()
    return piece


def _draft(db, piece: Piece) -> Certificate:
    cert = Certificate(piece_id=piece.id, status=CertificateStatus.draft)
    db.add(cert)
    db.flush()
    return cert


def _outputs(exc: BaseException) -> list[str]:
    return [
        str(exc),
        repr(exc),
        "".join(traceback.format_exception(exc)),  # full chain, as a server log would print it
        repr(exc.args),
    ]


def _database_password() -> str | None:
    password = make_url(get_settings().database_url).password
    # a 1-2 character password would match ordinary words; only meaningful when distinctive
    return password if password and len(password) >= 6 else None


def assert_safe(exc: BaseException) -> None:
    assert exc.__cause__ is None
    assert exc.__context__ is None
    secrets = [CANARY_TOKEN, CANARY_HASH, *(p for p in [_database_password()] if p)]
    for output in _outputs(exc):
        for secret in secrets:
            assert secret not in output
        for marker in _FORBIDDEN_DB_TEXT:
            assert marker not in output


# --- token_hash collision: UniqueViolation, DETAIL "Key (token_hash)=(...)" ----


def _colliding_activation(db_session):
    """Piece A holds an active certificate whose hash is CANARY_HASH; piece
    B's activation is forced to generate the same token."""
    piece_a, piece_b = _piece(db_session, "safety-collision-a"), _piece(db_session, "safety-collision-b")
    draft_a, draft_b = _draft(db_session, piece_a), _draft(db_session, piece_b)
    with patch.object(certificate_service, "generate_certificate_token", return_value=CANARY_TOKEN):
        activate_certificate(db_session, draft_a)
        return draft_b, lambda: activate_certificate(db_session, draft_b)


def test_premise_raw_unique_violation_detail_contains_the_token_hash(db_session):
    piece_a, piece_b = _piece(db_session, "premise-a"), _piece(db_session, "premise-b")
    db_session.add(Certificate(piece_id=piece_a.id, status=CertificateStatus.active, token_hash=CANARY_HASH))
    db_session.flush()
    db_session.add(Certificate(piece_id=piece_b.id, status=CertificateStatus.active, token_hash=CANARY_HASH))
    with pytest.raises(IntegrityError) as raw:
        with db_session.begin_nested():
            db_session.flush()
    assert CANARY_HASH in str(raw.value)  # this is what F-10 is about


def test_token_hash_collision_is_translated_without_database_detail(db_session, caplog):
    draft_b, collide = _colliding_activation(db_session)

    with caplog.at_level(logging.DEBUG):
        with patch.object(certificate_service, "generate_certificate_token", return_value=CANARY_TOKEN):
            with pytest.raises(CertificateLifecycleIntegrityError) as excinfo:
                collide()
        # what a server does with an unhandled exception
        logging.getLogger("test.server").error("unhandled", exc_info=excinfo.value)

    exc = excinfo.value
    assert_safe(exc)
    assert isinstance(exc, LifecycleIntegrityError) and isinstance(exc, LifecycleError)
    assert isinstance(exc, CertificateServiceError)
    assert not isinstance(exc, LifecycleConflict)  # a collision is not a race
    assert exc.constraint == "certificate_token_hash_key" and exc.sqlstate == "23505"
    assert CANARY_HASH not in caplog.text and CANARY_TOKEN not in caplog.text
    for marker in _FORBIDDEN_DB_TEXT:
        assert marker not in caplog.text
    # the failed activation was rolled back to its SAVEPOINT; the session works
    assert db_session.execute(text("select 1")).scalar_one() == 1
    assert draft_b.status == CertificateStatus.draft and draft_b.token_hash is None


def test_token_hash_collision_inside_rotate_is_translated_too(db_session):
    piece = _piece(db_session, "safety-rotate")
    draft = _draft(db_session, piece)
    with patch.object(certificate_service, "generate_certificate_token", return_value=CANARY_TOKEN):
        activate_certificate(db_session, draft)
        with pytest.raises(CertificateLifecycleIntegrityError) as excinfo:
            rotate_certificate(db_session, piece.id)
    assert_safe(excinfo.value)
    assert draft.status == CertificateStatus.active  # rotation rolled back


# --- CheckViolation: DETAIL "Failing row contains (..., <token_hash>, ...)" ----


def test_premise_raw_check_violation_detail_contains_the_failing_row_with_token_hash(db_session):
    piece = _piece(db_session, "premise-check")
    cert = Certificate(piece_id=piece.id, status=CertificateStatus.active, token_hash=CANARY_HASH)
    db_session.add(cert)
    db_session.flush()
    with pytest.raises(IntegrityError) as raw:
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE certificate SET status = 'revoked' WHERE id = :id"), {"id": cert.id}
            )
    assert "Failing row contains" in str(raw.value) and CANARY_HASH in str(raw.value)


def test_check_violation_row_dump_never_reaches_the_lifecycle_error(db_session):
    piece = _piece(db_session, "safety-check")
    draft = _draft(db_session, piece)
    with patch.object(certificate_service, "generate_certificate_token", return_value=CANARY_TOKEN):
        activate_certificate(db_session, draft)

    # revoked_at=None violates ck_certificate_revoked_at_matches_status on a
    # row that carries the canary token_hash.
    with patch.object(certificate_service, "datetime") as fake_datetime:
        fake_datetime.now.return_value = None
        with pytest.raises(CertificateLifecycleIntegrityError) as excinfo:
            revoke_certificate(db_session, draft)

    assert_safe(excinfo.value)
    assert excinfo.value.constraint == "ck_certificate_revoked_at_matches_status"
    assert excinfo.value.sqlstate == "23514"
    assert db_session.execute(text("select 1")).scalar_one() == 1


# --- the translator itself: constraint name / SQLSTATE only ---------------------


class _FakeDiag:
    def __init__(self, constraint_name):
        self.constraint_name = constraint_name


class _FakeOrig(Exception):
    """Stands in for the driver error: carries a poisoned message and DETAIL."""

    def __init__(self, sqlstate, constraint_name=None):
        super().__init__(f"DETAIL: Key (token_hash)=({CANARY_HASH}) already exists.")
        self.sqlstate = sqlstate
        self.diag = _FakeDiag(constraint_name)


def _translate(db_session, exc, *, known=None):
    def work():
        raise exc

    return run_in_savepoint(
        db_session,
        work,
        conflict=certificate_service.CertificateLifecycleConflict,
        integrity=CertificateLifecycleIntegrityError,
        known=known,
    )


def test_known_constraint_maps_to_its_domain_error_without_chain(db_session):
    piece_id = "piece-1"
    known = {"uq_x": lambda: certificate_service.ActiveCertificateAlreadyExists(f"Piece {piece_id} taken.")}
    exc = IntegrityError("UPDATE ...", {}, _FakeOrig("23505", "uq_x"))
    with pytest.raises(certificate_service.ActiveCertificateAlreadyExists) as excinfo:
        _translate(db_session, exc, known=known)
    assert_safe(excinfo.value)


@pytest.mark.parametrize("hostile_name", [f"x' OR '{CANARY_HASH}", "has space", "a" * 64, "", None])
def test_constraint_name_is_echoed_only_if_it_is_a_plain_identifier(db_session, hostile_name):
    exc = IntegrityError("UPDATE ...", {}, _FakeOrig("23514", hostile_name))
    with pytest.raises(CertificateLifecycleIntegrityError) as excinfo:
        _translate(db_session, exc)
    assert excinfo.value.constraint is None
    assert_safe(excinfo.value)


@pytest.mark.parametrize("sqlstate", ["40P01", "55P03"])
def test_deadlock_and_lock_timeout_sqlstates_map_to_conflict(db_session, sqlstate):
    exc = OperationalError("SELECT ...", {}, _FakeOrig(sqlstate))
    with pytest.raises(certificate_service.CertificateLifecycleConflict) as excinfo:
        _translate(db_session, exc)
    assert_safe(excinfo.value)


def test_other_operational_errors_are_not_swallowed(db_session):
    exc = OperationalError("SELECT ...", {}, _FakeOrig("08006"))
    with pytest.raises(OperationalError):
        _translate(db_session, exc)


def test_errors_raised_by_the_work_itself_pass_through_untouched(db_session):
    class Marker(Exception):
        pass

    with pytest.raises(Marker):
        _translate(db_session, Marker("boom"))


# --- API boundary: a test-local route, no application change --------------------


def test_unhandled_lifecycle_error_yields_generic_api_body_and_clean_server_traceback(db_session):
    draft_b, collide = _colliding_activation(db_session)
    server_side: list[str] = []

    app = FastAPI()
    register_exception_handlers(app)
    app.dependency_overrides[get_db] = lambda: db_session

    @app.get("/probe")
    def probe(db=Depends(get_db)):
        try:
            with patch.object(certificate_service, "generate_certificate_token", return_value=CANARY_TOKEN):
                collide()
        except LifecycleError as exc:
            server_side.append("".join(traceback.format_exception(exc)))
            raise

    response = TestClient(app, raise_server_exceptions=False).get("/probe")

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}
    for text_out in (response.text, *server_side):
        assert CANARY_HASH not in text_out and CANARY_TOKEN not in text_out
        for marker in _FORBIDDEN_DB_TEXT:
            assert marker not in text_out
    assert server_side, "the route must have raised a lifecycle error"


# --- static guard: no logging / exception echo / chaining in lifecycle code -----

_LIFECYCLE_MODULES = (lifecycle_service, certificate_service, nfc_service)


@pytest.mark.parametrize("module", _LIFECYCLE_MODULES, ids=lambda m: m.__name__)
def test_lifecycle_modules_never_log_echo_or_chain_exceptions(module):
    tree = ast.parse(Path(module.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] + [getattr(node, "module", None) or ""]
            assert not any(name.split(".")[0] == "logging" for name in names)
        if isinstance(node, ast.Raise):
            assert node.cause is None, f"{module.__name__}:{node.lineno} chains an exception"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "print":
                pytest.fail(f"{module.__name__}:{node.lineno} prints")
            if node.func.id in {"str", "repr"} and node.args:
                arg = node.args[0]
                assert not (isinstance(arg, ast.Name) and arg.id in {"exc", "e", "err", "error"}), (
                    f"{module.__name__}:{node.lineno} echoes an exception"
                )
        if isinstance(node, ast.Name):
            assert node.id not in {"logger", "log"}
