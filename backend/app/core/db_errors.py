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


@_total(_UNMATCHED_ROUTE)
def _route_template(request: Request) -> str:
    # `scope["route"]` is set by the router once a route matched. Its `path`
    # is the template. Never fall back to `request.url.path`.
    path = getattr(request.scope.get("route"), "path", None)
    if isinstance(path, str) and _ROUTE_TEMPLATE_RE.fullmatch(path):
        return path
    return _UNMATCHED_ROUTE


async def database_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log one sanitized line and return the standard generic 500 envelope
    (API_CONTRACT.md section 10; identical to the body of
    ``app.core.errors.unhandled_exception_handler``). Never raises."""
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
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
    )
