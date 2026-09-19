"""Audit finding F-10, global scope: a database exception raised anywhere in a
request (not just inside the lifecycle services) must not reach the process
log with PostgreSQL DETAIL, the SQL statement or row values such as
``token_hash``. The boundary is ``app.core.db_errors.database_exception_handler``.

These are in-process (TestClient) tests against real PostgreSQL failures. What
Uvicorn itself prints is proven separately, with a real server process, by
tests/test_global_db_error_uvicorn.py.

Every "absent" assertion is paired with a premise check that the raw database
error really contains the canary, otherwise the tests could pass for the wrong
reason.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import (
    DataError,
    DBAPIError,
    IntegrityError,
    InterfaceError,
    InternalError,
    InvalidRequestError,
    NotSupportedError,
    OperationalError,
    PendingRollbackError,
    ProgrammingError,
    SQLAlchemyError,
    StatementError,
)
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import NoResultFound
from starlette.requests import Request

from app.api.deps import get_db
from app.core import db_errors
from app.core.config import get_settings
from app.core.db_errors import DATABASE_EXCEPTION_TYPES, database_exception_handler
from app.core.errors import register_exception_handlers, unhandled_exception_handler
from app.db.base import SessionLocal
from app.main import app as real_app
from app.models import Artisan
from tests import db_failure_helpers as failures

CANARY_HASH = "CanaryGlobalHash4c1f0e9a"
CANARY_SQL = "CANARY_SQL_LITERAL_9be2"
CANARY_COLUMN = "no_such_column_canary_7d2"
CANARY_PATH_TOKEN = "CanaryPathTokenXXXXXXXXXXXXXXXXXXXXXXXXXX"
CANARY_QUERY = "CANARY_QUERY_VALUE_31ac"
CANARY_EXTRACTION = "CANARY_EXTRACTION_FAILURE"
CANARY_RAW_VALUE = "CANARYRAWVALUE8e41"

GENERIC_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}

# Text that must never appear in a log line or a response for a DB failure.
_FORBIDDEN = (
    CANARY_HASH,
    CANARY_SQL,
    CANARY_COLUMN,
    CANARY_PATH_TOKEN,
    CANARY_QUERY,
    CANARY_EXTRACTION,
    CANARY_RAW_VALUE,
    "DETAIL",
    "Key (",
    "[SQL",
    "SQL parameters",
    "Original exception",
    "Traceback",
    "psycopg",
    "sqlalchemy",
    "postgresql://",
)


def _db_error_records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "app.db_errors"]


def _assert_safe_record(record: logging.LogRecord) -> None:
    # The invariant: only the fixed format and five sanitized strings - no
    # exception object, no exc_info/stack_info, nothing raw.
    assert record.levelno == logging.ERROR
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.stack_info is None
    assert isinstance(record.args, tuple) and len(record.args) == 5
    assert all(type(arg) is str for arg in record.args)
    message = record.getMessage()
    assert message.startswith("event=db_error category=")
    assert "\n" not in message
    for marker in _FORBIDDEN:
        assert marker not in message


def _assert_generic_500(response) -> None:
    assert response.status_code == 500
    assert response.json() == GENERIC_500
    for marker in _FORBIDDEN:
        assert marker not in response.text


# --- Probe application --------------------------------------------------------------------

_constructed: dict[str, BaseException] = {}


def _commit_on_teardown():
    session = SessionLocal()
    try:
        yield session
        failures.insert_duplicate_token_hash(session, CANARY_HASH)  # fails after the endpoint returned
        session.commit()
    finally:
        session.close()


def _build_probe_app() -> FastAPI:
    probe = FastAPI()
    register_exception_handlers(probe)

    @probe.get("/probe/integrity")
    def integrity(db: Session = Depends(get_db)):
        failures.insert_duplicate_token_hash(db, CANARY_HASH)

    @probe.get("/probe/operational")
    def operational(db: Session = Depends(get_db)):
        failures.statement_timeout_failure(db, CANARY_SQL)

    @probe.get("/probe/programming")
    def programming(db: Session = Depends(get_db)):
        failures.undefined_column_failure(db, CANARY_COLUMN)

    @probe.get("/probe/data")
    def data(db: Session = Depends(get_db)):
        failures.invalid_text_representation_failure(db)

    @probe.get("/probe/pending-rollback")
    def pending_rollback(db: Session = Depends(get_db)):
        failures.pending_rollback_after_swallowed_failure(db, CANARY_HASH)

    @probe.get("/probe/raw-driver")
    def raw_driver(db: Session = Depends(get_db)):
        failures.raw_driver_unique_violation(db, CANARY_RAW_VALUE)

    @probe.get("/probe/teardown-commit")
    def teardown_commit(db: Session = Depends(_commit_on_teardown)):
        return {"ok": True}

    @probe.get("/probe/constructed")
    def constructed():
        raise _constructed["exc"]

    @probe.get("/tokens/{token}")
    def by_token(token: str):
        raise _constructed["exc"]

    return probe


@pytest.fixture(scope="module")
def probe_app() -> FastAPI:
    return _build_probe_app()


@pytest.fixture()
def client(probe_app) -> TestClient:
    # raise_server_exceptions=True (the default) on purpose: if the handler let
    # an exception escape, the test would raise instead of seeing a 500.
    return TestClient(probe_app)


# --- Real database failures ------------------------------------------------------------------

# route, category, sqlstate, constraint
REAL_FAILURES = [
    ("/probe/integrity", "integrity", "23505", "certificate_token_hash_key"),
    ("/probe/operational", "operational", "57014", "-"),
    ("/probe/programming", "programming", "42703", "-"),
    ("/probe/data", "data", "22P02", "-"),
    ("/probe/pending-rollback", "pending_rollback", "-", "-"),
    ("/probe/raw-driver", "driver", "23505", "dbf_raw_unique_v_key"),
    # Dependency teardown failing after the endpoint returned (pinned FastAPI
    # 0.115.6 still finishes the request inside ExceptionMiddleware).
    ("/probe/teardown-commit", "integrity", "23505", "certificate_token_hash_key"),
]


@pytest.mark.parametrize("route,category,sqlstate,constraint", REAL_FAILURES)
def test_real_database_failure_returns_generic_500_and_one_safe_log_line(
    client, caplog, route, category, sqlstate, constraint
):
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        response = client.get(route)

    _assert_generic_500(response)
    records = _db_error_records(caplog)
    assert len(records) == 1
    _assert_safe_record(records[0])
    assert records[0].getMessage() == (
        f"event=db_error category={category} sqlstate={sqlstate} "
        f"constraint={constraint} method=GET route={route}"
    )
    # Nothing else in the captured logs (SQLAlchemy, uvicorn-style loggers...)
    # carries the canaries either.
    for marker in _FORBIDDEN:
        assert marker not in caplog.text


def test_premise_the_raw_database_errors_really_contain_the_canaries():
    # Without this, the absence assertions above could pass vacuously.
    db = SessionLocal()
    try:
        with pytest.raises(IntegrityError) as integrity:
            failures.insert_duplicate_token_hash(db, CANARY_HASH)
        assert CANARY_HASH in str(integrity.value) and "DETAIL" in str(integrity.value)
    finally:
        db.close()

    db = SessionLocal()
    try:
        with pytest.raises(PendingRollbackError) as pending:
            failures.pending_rollback_after_swallowed_failure(db, CANARY_HASH)
        assert CANARY_HASH in str(pending.value) and "Original exception" in str(pending.value)
    finally:
        db.close()

    db = SessionLocal()
    try:
        with pytest.raises(OperationalError) as operational:
            failures.statement_timeout_failure(db, CANARY_SQL)
        assert CANARY_SQL in str(operational.value)
    finally:
        db.close()

    db = SessionLocal()
    try:
        with pytest.raises(psycopg.errors.UniqueViolation) as raw:
            failures.raw_driver_unique_violation(db, CANARY_RAW_VALUE)
        assert CANARY_RAW_VALUE in str(raw.value) and "DETAIL" in str(raw.value)
    finally:
        db.close()


def test_the_probe_failures_persist_nothing():
    with SessionLocal() as db:
        assert db.execute(select(Artisan).where(Artisan.slug.like("dbf-%"))).first() is None


# --- Response contract -----------------------------------------------------------------------


def test_database_500_is_byte_identical_to_the_generic_500(client):
    db_response = client.get("/probe/integrity")
    generic = asyncio.run(unhandled_exception_handler(_request(), RuntimeError("x")))

    assert db_response.status_code == generic.status_code == 500
    assert db_response.content == generic.body
    assert db_response.headers["content-type"] == "application/json"


def test_resolve_database_500_keeps_no_store_and_cors_headers(caplog):
    # The DB handler runs inside CORSMiddleware and ResolveNoStoreMiddleware
    # (unlike the catch-all 500), so /resolve now carries both.
    origin = get_settings().cors_allowed_origins_list[0]
    token = "CanaryResolveTok" + "X" * 27
    assert len(token) == 43

    def broken_db():
        raise OperationalError("SELECT 1", None, Exception(f"could not connect {CANARY_HASH}"))
        yield  # pragma: no cover

    real_app.dependency_overrides[get_db] = broken_db
    try:
        with caplog.at_level(logging.INFO, logger="app.db_errors"):
            response = TestClient(real_app).post(
                "/api/v1/certificates/resolve", json={"token": token}, headers={"Origin": origin}
            )
    finally:
        real_app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 500
    assert response.json() == GENERIC_500
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == origin
    [record] = _db_error_records(caplog)
    _assert_safe_record(record)
    assert record.getMessage() == (
        "event=db_error category=operational sqlstate=- constraint=- "
        "method=POST route=/api/v1/certificates/resolve"
    )
    assert token not in caplog.text and token not in response.text
    assert CANARY_HASH not in caplog.text


# --- Categories (constructed exceptions) -------------------------------------------------------


class _DriverError(Exception):
    """Stands in for ``exc.orig``: carries a SQLSTATE and a constraint name."""

    def __init__(self, sqlstate=None, constraint=None):
        super().__init__(f"DETAIL: Key (token_hash)=({CANARY_HASH}) already exists.")
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(constraint_name=constraint)


def _dbapi(cls, sqlstate="23505", constraint="uq_something"):
    return cls(f"SELECT {CANARY_SQL}", None, _DriverError(sqlstate, constraint))


@pytest.mark.parametrize(
    "exc,category",
    [
        (_dbapi(IntegrityError), "integrity"),
        (_dbapi(OperationalError), "operational"),
        (_dbapi(ProgrammingError), "programming"),
        (_dbapi(DataError), "data"),
        (_dbapi(InterfaceError), "interface"),
        (_dbapi(InternalError), "internal"),
        (_dbapi(NotSupportedError), "not_supported"),
        (_dbapi(DBAPIError), "dbapi"),
        (PendingRollbackError(f"Original exception was: DETAIL {CANARY_HASH}"), "pending_rollback"),
        (psycopg.errors.UniqueViolation(f"DETAIL: Key (token_hash)=({CANARY_HASH})"), "driver"),
        (psycopg.Error(f"raw {CANARY_HASH}"), "driver"),
    ],
    ids=lambda v: v if isinstance(v, str) else type(v).__name__,
)
def test_categories_are_derived_from_the_class(client, caplog, exc, category):
    _constructed["exc"] = exc
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        response = client.get("/probe/constructed")

    _assert_generic_500(response)
    [record] = _db_error_records(caplog)
    _assert_safe_record(record)
    assert record.args[0] == category
    assert CANARY_HASH not in caplog.text and CANARY_SQL not in caplog.text


def test_sqlstate_and_constraint_of_the_driver_error_are_reported(client, caplog):
    _constructed["exc"] = _dbapi(IntegrityError, "23505", "uq_certificate_one_active_per_piece")
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        client.get("/probe/constructed")
    assert _db_error_records(caplog)[0].getMessage() == (
        "event=db_error category=integrity sqlstate=23505 "
        "constraint=uq_certificate_one_active_per_piece method=GET route=/probe/constructed"
    )


@pytest.mark.parametrize(
    "hostile",
    [
        "x\nevent=forged category=integrity",
        "ok_name\n",
        "a b",
        "a-b",
        "a" * 64,
        "x;DROP TABLE certificate",
        "",
        f"key ({CANARY_HASH})",
        123,
        None,
        b"bytes_name",
    ],
    ids=repr,
)
def test_hostile_constraint_name_is_dropped(client, caplog, hostile):
    _constructed["exc"] = _dbapi(IntegrityError, "23505", hostile)
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        response = client.get("/probe/constructed")

    _assert_generic_500(response)
    [record] = _db_error_records(caplog)
    _assert_safe_record(record)
    assert record.args[2] == "-"
    assert "forged" not in caplog.text and "DROP" not in caplog.text


@pytest.mark.parametrize(
    "hostile",
    ["23505\n", "2350", "235050", "abcde", "23 05", "2350;", f"{CANARY_HASH}", 23505, None],
    ids=repr,
)
def test_hostile_sqlstate_is_dropped(client, caplog, hostile):
    _constructed["exc"] = _dbapi(IntegrityError, hostile, "uq_ok")
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        client.get("/probe/constructed")
    [record] = _db_error_records(caplog)
    _assert_safe_record(record)
    assert record.args[1] == "-"


# --- Totality: the handler must never raise ------------------------------------------------------


class _ExplodingOrig(IntegrityError):
    @property
    def orig(self):
        raise RuntimeError(CANARY_EXTRACTION)

    @orig.setter
    def orig(self, value):
        pass


class _ExplodingDiag(Exception):
    sqlstate = "23505"

    @property
    def diag(self):
        raise RuntimeError(CANARY_EXTRACTION)


def test_metadata_extraction_failure_falls_back_to_fixed_values(client, caplog):
    _constructed["exc"] = _ExplodingOrig(f"SELECT {CANARY_SQL}", None, Exception("unused"))
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        response = client.get("/probe/constructed")

    _assert_generic_500(response)
    [record] = _db_error_records(caplog)
    _assert_safe_record(record)
    assert record.getMessage() == (
        "event=db_error category=integrity sqlstate=- constraint=- method=GET route=/probe/constructed"
    )


def test_a_raising_diag_only_loses_the_constraint(client, caplog):
    orig = _ExplodingDiag("driver")
    _constructed["exc"] = OperationalError(f"SELECT {CANARY_SQL}", None, orig)
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        response = client.get("/probe/constructed")

    _assert_generic_500(response)
    [record] = _db_error_records(caplog)
    assert record.getMessage() == (
        "event=db_error category=operational sqlstate=23505 constraint=- method=GET route=/probe/constructed"
    )


def test_a_failing_logger_never_turns_into_a_raised_exception(client, monkeypatch):
    class _BrokenLogger:
        def error(self, *args, **kwargs):
            raise RuntimeError(CANARY_EXTRACTION)

    monkeypatch.setattr(db_errors, "logger", _BrokenLogger())
    _constructed["exc"] = _dbapi(IntegrityError)

    response = client.get("/probe/constructed")  # would raise if the handler propagated

    _assert_generic_500(response)


def test_database_error_is_fully_consumed_not_reraised(probe_app):
    # ServerErrorMiddleware re-raises after responding (that is what feeds
    # Uvicorn's traceback log). The class-specific handler must not.
    _constructed["exc"] = _dbapi(IntegrityError)
    TestClient(probe_app, raise_server_exceptions=True).get("/probe/constructed")  # no raise

    _constructed["exc"] = RuntimeError("control")
    with pytest.raises(RuntimeError, match="control"):
        TestClient(probe_app, raise_server_exceptions=True).get("/probe/constructed")


# --- Route privacy ---------------------------------------------------------------------------------


def test_route_is_the_template_never_the_request_path_or_query(client, caplog):
    _constructed["exc"] = _dbapi(OperationalError)
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        response = client.get(f"/tokens/{CANARY_PATH_TOKEN}?q={CANARY_QUERY}")

    _assert_generic_500(response)
    [record] = _db_error_records(caplog)
    _assert_safe_record(record)
    assert record.args[4] == "/tokens/{token}"
    assert CANARY_PATH_TOKEN not in caplog.text and CANARY_QUERY not in caplog.text


def _request(*, method="GET", route=None, path="/whatever") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": f"q={CANARY_QUERY}".encode(),
        "headers": [(b"x-secret", CANARY_HASH.encode())],
        "client": ("203.0.113.9", 1234),
    }
    if route is not None:
        scope["route"] = route
    return Request(scope)


class _RouteWithRaisingPath:
    @property
    def path(self):
        raise RuntimeError(CANARY_EXTRACTION)


@pytest.mark.parametrize(
    "route",
    [
        None,
        SimpleNamespace(path=None),
        SimpleNamespace(path=""),
        SimpleNamespace(path="no-leading-slash"),
        SimpleNamespace(path="/x\nevent=forged"),
        SimpleNamespace(path="/x y"),
        SimpleNamespace(path=f"/private/{CANARY_PATH_TOKEN}?q=1"),
        SimpleNamespace(path="/" + "a" * 300),
        SimpleNamespace(path=b"/bytes"),
        _RouteWithRaisingPath(),
    ],
    ids=lambda r: type(r).__name__ + (":" + repr(getattr(r, "path", "")) if isinstance(r, SimpleNamespace) else ""),
)
def test_unsafe_or_missing_route_becomes_unmatched_and_never_the_request_path(caplog, route):
    request = _request(route=route, path=f"/private/{CANARY_PATH_TOKEN}")
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        response = asyncio.run(database_exception_handler(request, _dbapi(IntegrityError)))

    assert response.status_code == 500 and json.loads(response.body) == GENERIC_500
    [record] = _db_error_records(caplog)
    _assert_safe_record(record)
    assert record.args[4] == "<unmatched>"
    assert CANARY_PATH_TOKEN not in caplog.text and CANARY_QUERY not in caplog.text
    assert "203.0.113.9" not in caplog.text and CANARY_HASH not in caplog.text


@pytest.mark.parametrize(
    "path", ["/", "/health", "/api/v1/pieces/{slug}", "/api/v1/certificates/resolve", "/files/{p:path}"]
)
def test_real_route_templates_pass_through(caplog, path):
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        asyncio.run(database_exception_handler(_request(route=SimpleNamespace(path=path)), _dbapi(IntegrityError)))
    assert _db_error_records(caplog)[0].args[4] == path


@pytest.mark.parametrize("method,expected", [("GET", "GET"), ("POST", "POST"), ("BREW", "OTHER"), ("get", "OTHER")])
def test_method_is_allowlisted(caplog, method, expected):
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        asyncio.run(database_exception_handler(_request(method=method), _dbapi(IntegrityError)))
    assert _db_error_records(caplog)[0].args[3] == expected


# --- The boundary is not a generic exception sink --------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("programmer error"),
        ValueError("bad value"),
        NoResultFound("No row was found when one was required"),
        InvalidRequestError("misuse of the session"),
        StatementError("bind processing failed", "SELECT 1", None, ValueError("bad bind")),
        SQLAlchemyError("some other sqlalchemy error"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_non_database_errors_still_take_the_generic_path_with_their_traceback(probe_app, caplog, exc):
    _constructed["exc"] = exc

    # Visible to the server: re-raised (this is what Uvicorn logs as a traceback).
    with caplog.at_level(logging.INFO, logger="app.db_errors"):
        with pytest.raises(type(exc)):
            TestClient(probe_app, raise_server_exceptions=True).get("/probe/constructed")
        # And still the standard generic body for the client.
        response = TestClient(probe_app, raise_server_exceptions=False).get("/probe/constructed")

    _assert_generic_500(response)
    assert _db_error_records(caplog) == []


def test_only_the_approved_classes_are_registered_and_they_do_not_overlap(probe_app):
    assert set(DATABASE_EXCEPTION_TYPES) == {DBAPIError, PendingRollbackError, psycopg.Error}
    registered = {
        cls for cls, handler in probe_app.exception_handlers.items() if handler is database_exception_handler
    }
    assert registered == set(DATABASE_EXCEPTION_TYPES)
    assert probe_app.exception_handlers[Exception] is unhandled_exception_handler
    for cls in (Exception, SQLAlchemyError, StatementError, InvalidRequestError):
        assert probe_app.exception_handlers.get(cls) is not database_exception_handler

    types = list(DATABASE_EXCEPTION_TYPES)
    for a in types:
        for b in types:
            if a is not b:
                assert not issubclass(a, b), f"{a.__name__} overlaps {b.__name__}"


def test_the_real_app_registers_the_same_handlers():
    for cls in DATABASE_EXCEPTION_TYPES:
        assert real_app.exception_handlers[cls] is database_exception_handler


# --- Static guard on the new module ------------------------------------------------------------------

_LOGGER_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log", "fatal"}
_SANITIZED_ARG_HELPERS = {"_category", "_sqlstate", "_constraint", "_method", "_route_template"}


def test_db_errors_module_never_logs_or_echoes_an_exception():
    tree = ast.parse(Path(db_errors.__file__).read_text())
    logger_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] + [getattr(node, "module", None) or ""]
            assert not any(name.split(".")[0] in {"traceback", "sys", "warnings"} for name in names)
        if isinstance(node, ast.keyword):
            assert node.arg not in {"exc_info", "stack_info", "extra"}
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"exception", "format_exc", "print_exc", "__cause__", "__context__"}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"str", "repr", "print", "format", "vars", "dir"}, (
                f"line {node.lineno} stringifies something"
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            assert node.func.attr in _LOGGER_METHODS
            logger_calls += 1
            assert not node.keywords
            assert isinstance(node.args[0], ast.Name) and node.args[0].id == "_LOG_FORMAT"
            for arg in node.args[1:]:
                assert isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
                assert arg.func.id in _SANITIZED_ARG_HELPERS
    assert logger_calls == 1
