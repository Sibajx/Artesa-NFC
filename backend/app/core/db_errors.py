"""Global database-exception boundary (audit finding F-10, global scope).

PostgreSQL embeds row values in ``DETAIL`` (for ``certificate`` that includes
``token_hash``) and SQLAlchemy appends the SQL statement, so the text of a raw
database exception must never reach the process log
(docs/SECURITY.md sections 12.2 and 13). The lifecycle services already
translate the errors they expect (``app.services.lifecycle``); this module
covers every *other* place a database failure can surface during a request:
a caller's own ``flush``/``commit``, any query in a route, ``Session.close()``
in the ``get_db`` teardown.

Why a plain exception handler is enough (no middleware, no server patching):
Starlette special-cases a handler registered for ``Exception``/500 - it runs
in the outermost ``ServerErrorMiddleware``, which sends the response and then
re-raises so the server can log the traceback (that re-raise is what made
Uvicorn print ``DETAIL``). A handler registered for a *specific* class runs in
the inner ``ExceptionMiddleware`` instead, which consumes the exception: nothing
is re-raised, so nothing chains and nothing reaches Uvicorn.

Registered classes (see ``DATABASE_EXCEPTION_TYPES``; they do not overlap, so
no request can match two handlers):

* ``sqlalchemy.exc.DBAPIError`` - ``IntegrityError``, ``OperationalError``,
  ``ProgrammingError``, ``DataError``, ``InterfaceError``, ``InternalError``,
  ``NotSupportedError`` and the base class itself.
* ``sqlalchemy.exc.PendingRollbackError`` - not a ``DBAPIError``, yet its
  message embeds "Original exception was: ..." including ``DETAIL``.
* ``psycopg.Error`` - raw driver errors that bypass SQLAlchemy's wrapping.

Deliberately NOT registered: ``Exception``, ``SQLAlchemyError``,
``StatementError``, ``InvalidRequestError``. ``RuntimeError``,
``NoResultFound`` and other programmer errors keep flowing through the generic
500 path with their normal server traceback; this boundary is not a catch-all.

Invariants (asserted by tests/test_global_db_error_boundary.py and the real
Uvicorn test):

* The logger is only ever called with a fixed format and five sanitized
  strings - never the exception, ``exc_info``, ``stack_info`` or any raw
  database text. Correctness does not depend on how logging is configured
  (formatting/output is left to the normal logging setup).
* The handler is total: every extraction step falls back to a fixed value,
  and the logging call itself is guarded. If it raised, the original database
  exception would become its ``__context__`` and Uvicorn would print it,
  restoring the leak.
* The route is the matched route *template* (``/api/v1/pieces/{slug}``),
  never the request path, so a private value in a path parameter cannot be
  logged. No query string, body, header or client address is read.

Out of scope (documented in docs/SECURITY.md section 13): database errors
raised outside a request (background threads, startup, connection-pool
internals) are not covered by an HTTP exception boundary.
"""
from __future__ import annotations

import functools
import logging
import re
from collections.abc import Callable
from typing import Any

import psycopg
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import (
    DataError,
    DBAPIError,
    IntegrityError,
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    PendingRollbackError,
    ProgrammingError,
)

logger = logging.getLogger("app.db_errors")

# Every class the global boundary handles. Registered one handler per class in
# app.core.errors; categorization below is by isinstance, not by handler.
DATABASE_EXCEPTION_TYPES: tuple[type[BaseException], ...] = (
    DBAPIError,
    PendingRollbackError,
    psycopg.Error,
)

_LOG_FORMAT = "event=db_error category=%s sqlstate=%s constraint=%s method=%s route=%s"

_UNKNOWN = "-"
_UNMATCHED_ROUTE = "<unmatched>"

# Same shapes the lifecycle services accept (a SQLSTATE and a schema
# identifier); anything else is dropped, so nothing else can ride along.
_SQLSTATE_RE = re.compile(r"[0-9A-Z]{5}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_]{1,63}")
# A route *template*: path segments plus `{name}` / `{name:convertor}`.
# Whitespace, control characters and quotes are rejected, so a forged value
# cannot inject a fake log field.
_ROUTE_TEMPLATE_RE = re.compile(r"/[A-Za-z0-9_\-./{}:]{0,199}")

_HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

# Most specific first is irrelevant (the classes are siblings); the tuple is
# only a lookup table.
_DBAPI_CATEGORIES: tuple[tuple[type[DBAPIError], str], ...] = (
    (IntegrityError, "integrity"),
    (OperationalError, "operational"),
    (ProgrammingError, "programming"),
    (DataError, "data"),
    (InterfaceError, "interface"),
    (InternalError, "internal"),
    (NotSupportedError, "not_supported"),
)


def _total(default: str) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Make an extractor total: any failure while inspecting the exception or
    request (a hostile ``orig``, a raising property, ...) yields ``default``
    instead of propagating."""

    def decorate(extract: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(extract)
        def guarded(*args: Any) -> str:
            try:
                return extract(*args)
            except Exception:
                return default

        return guarded

    return decorate


def _driver_error(exc: BaseException) -> Any:
    """The psycopg exception behind ``exc`` (SQLAlchemy keeps it in ``orig``;
    a raw driver error is its own)."""
    if isinstance(exc, psycopg.Error):
        return exc
    return getattr(exc, "orig", None)


@_total("unknown")
def _category(exc: BaseException) -> str:
    if isinstance(exc, PendingRollbackError):
        return "pending_rollback"
    if isinstance(exc, DBAPIError):
        for exc_type, name in _DBAPI_CATEGORIES:
            if isinstance(exc, exc_type):
                return name
        return "dbapi"
    if isinstance(exc, psycopg.Error):
        return "driver"
    return "unknown"


@_total(_UNKNOWN)
def _sqlstate(exc: BaseException) -> str:
    value = getattr(_driver_error(exc), "sqlstate", None)
    if isinstance(value, str) and _SQLSTATE_RE.fullmatch(value):
        return value
    return _UNKNOWN


@_total(_UNKNOWN)
def _constraint(exc: BaseException) -> str:
    diag = getattr(_driver_error(exc), "diag", None)
    value = getattr(diag, "constraint_name", None)
    if isinstance(value, str) and _IDENTIFIER_RE.fullmatch(value):
        return value
    return _UNKNOWN


@_total("OTHER")
def _method(request: Request) -> str:
    value = request.method
    return value if value in _HTTP_METHODS else "OTHER"


# A router prefix: static segments only, never a path parameter.
_STATIC_PREFIX_RE = re.compile(r"(?:/[A-Za-z0-9_\-]{1,63}){0,8}")


@_total(_UNMATCHED_ROUTE)
def _route_template(request: Request) -> str:
    # `scope["route"]` is set by the router once a route matched. Its `path`
    # is the template. Never fall back to `request.url.path`.
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if not (isinstance(path, str) and _ROUTE_TEMPLATE_RE.fullmatch(path)):
        return _UNMATCHED_ROUTE
    # FastAPI >= 0.13x keeps included routers nested: with a router included
    # inside another one (/api/v1 -> /artisans), `route.path` is relative to
    # its own router and lacks the outer prefix. The prefix is the part of the
    # requested path in front of the suffix the route's own regex matches; it
    # is only used when it is purely static, so a path parameter can never
    # reach the log. On FastAPI versions that flatten routes the suffix is the
    # whole path and the prefix is empty.
    regex = getattr(route, "path_regex", None)
    requested = request.scope.get("path", "")
    if regex is None or not isinstance(requested, str) or regex.fullmatch(requested):
        return path
    for cut in (i for i, ch in enumerate(requested) if ch == "/" and i > 0):
        if regex.fullmatch(requested[cut:]):
            prefix = requested[:cut]
            if _STATIC_PREFIX_RE.fullmatch(prefix) and _ROUTE_TEMPLATE_RE.fullmatch(prefix + path):
                return prefix + path
            break
    return _UNMATCHED_ROUTE


# N-02: failures that mean "the database cannot be reached right now" answer
# 503 instead of 500, so a monitor can tell an outage from a bug. /health
# already says so publicly; the body still carries no database text.
_UNAVAILABLE_SQLSTATE_CLASSES = ("08", "28", "53")  # connection, authorization, resources
_UNAVAILABLE_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})  # server shutting down / starting
UNAVAILABLE_503 = {"error": {"code": "service_unavailable",
                             "message": "The service is temporarily unavailable. Try again later."}}
RETRY_AFTER_SECONDS = "30"


def is_unavailable(exc: BaseException) -> bool:
    """True for a connection-level failure (no SQLSTATE, or an unavailable
    class); a failing query (lock timeout, cancel, constraint) is not."""
    try:
        if not isinstance(exc, (OperationalError, InterfaceError, psycopg.OperationalError, psycopg.InterfaceError)):
            return False
        state = _sqlstate(exc)
        return state == _UNKNOWN or state[:2] in _UNAVAILABLE_SQLSTATE_CLASSES or state in _UNAVAILABLE_SQLSTATES
    except Exception:
        return False


async def database_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log one sanitized line and return the standard generic 500 envelope
    (API_CONTRACT.md section 10; identical to the body of
    ``app.core.errors.unhandled_exception_handler``), or the generic 503 when
    the database is unreachable (N-02). Never raises."""
    try:
        logger.error(
            _LOG_FORMAT,
            _category(exc),
            _sqlstate(exc),
            _constraint(exc),
            _method(request),
            _route_template(request),
        )
    except Exception:
        # Logging must never be able to bring the leak path back.
        pass
    if is_unavailable(exc):
        return JSONResponse(status_code=503, content=UNAVAILABLE_503, headers={"Retry-After": RETRY_AFTER_SECONDS})
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
    )


# --- after the response has started (defence in depth, issue #121) -----------------


_AFTER_RESPONSE_ROUTE = "<after-response>"


def _database_error_in_chain(exc: BaseException | None) -> BaseException | None:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, DATABASE_EXCEPTION_TYPES):
            return exc
        exc = exc.__cause__ or exc.__context__
    return None


class DatabaseTracebackFilter(logging.Filter):
    """Keeps database text out of the server's own error log.

    The handlers above consume a database exception while the response can
    still be chosen. Newer Starlette re-raises one that happens after the
    response has started (e.g. a failing commit in a yield-dependency teardown,
    which ArtesaNFC does not do today) as ``RuntimeError(...) from exc``; the
    server would then print the chained PostgreSQL ``DETAIL``. This filter,
    installed on ``uvicorn.error``, rewrites any record whose exception chain
    carries a database error into the same single sanitized line, with no
    traceback. Other records (a programmer's RuntimeError) are untouched."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            exc = record.exc_info[1] if record.exc_info else None
            database_error = _database_error_in_chain(exc)
            if database_error is None:
                return True
            record.msg = _LOG_FORMAT
            record.args = (
                _category(database_error),
                _sqlstate(database_error),
                _constraint(database_error),
                "OTHER",
                _AFTER_RESPONSE_ROUTE,
            )
        except Exception:
            record.msg, record.args = "event=db_error category=unknown", ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def install_server_log_filter() -> None:
    """Idempotent: adds DatabaseTracebackFilter to the Uvicorn error logger."""
    server_logger = logging.getLogger("uvicorn.error")
    if not any(isinstance(f, DatabaseTracebackFilter) for f in server_logger.filters):
        server_logger.addFilter(DatabaseTracebackFilter())
