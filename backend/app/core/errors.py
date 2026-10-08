from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.db_errors import DATABASE_EXCEPTION_TYPES, database_exception_handler, install_server_log_filter

# API_CONTRACT.md section 10: standard public error envelope
# {"error": {"code": ..., "message": ...}}. Never leaks stack traces or
# database error text.

_DEFAULT_MESSAGES: dict[int, str] = {
    400: "The request could not be processed.",
    404: "The requested resource does not exist.",
    405: "This method is not allowed for this resource.",
    413: "The request body is too large.",
    429: "Too many requests.",
    500: "An unexpected error occurred.",
}

_DEFAULT_CODES: dict[int, str] = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    429: "rate_limited",
    500: "internal_error",
}


def _error_body(status_code: int, detail: object) -> dict:
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        return {"error": detail}
    # A plain string `detail` here is always Starlette/FastAPI's own default
    # phrase (e.g. "Not Found", "Method Not Allowed") for a framework-raised
    # exception, never an application one — the only HTTPException this app
    # raises directly (app/api/v1/common.py::not_found) uses a dict detail
    # and is handled by the branch above. Prefer our canonical public
    # message for known status codes so the wording is consistent instead of
    # leaking the framework's default phrase.
    if status_code in _DEFAULT_MESSAGES:
        message = _DEFAULT_MESSAGES[status_code]
    else:
        message = detail if isinstance(detail, str) else "An error occurred."
    code = _DEFAULT_CODES.get(status_code, "error")
    return {"error": {"code": code, "message": message}}


def error_response(status_code: int) -> JSONResponse:
    """The standard public envelope for a status the application answers
    itself, outside any route (e.g. ASGI middleware that must reject a
    request before the router or the body parser runs). Same body a raised
    ``HTTPException`` with that status would produce."""
    return JSONResponse(status_code=status_code, content=_error_body(status_code, ""))


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=_error_body(exc.status_code, exc.detail))


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = [
        {
            "field": ".".join(str(part) for part in err["loc"][1:]) or str(err["loc"][-1]),
            "reason": err["msg"],
        }
        for err in exc.errors()
    ]
    body = {
        "error": {
            "code": "validation_error",
            "message": "The request is invalid.",
            "details": details,
        }
    }
    return JSONResponse(status_code=422, content=body)


_GENERIC_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content=_GENERIC_500)


class UnhandledErrorMiddleware:
    """N-02: the generic 500 for a non-database error, answered *inside* the
    CORS and no-store layers.

    Without it the 500 comes from Starlette's ServerErrorMiddleware, the
    outermost layer, so /resolve and /api/admin answered it without
    ``Cache-Control: no-store`` and without CORS. This sends the same body,
    then re-raises: ServerErrorMiddleware sees a started response and only
    passes the exception on, so Uvicorn still logs the traceback exactly as
    before. Database errors never get here (their handlers consume them)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            if not started:
                body = json.dumps(_GENERIC_500, separators=(",", ":")).encode()
                await send({"type": "http.response.start", "status": 500, "headers": [
                    (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
            raise


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    # Database failures get their own class-specific handlers so Starlette runs
    # them in ExceptionMiddleware, which consumes the exception. The catch-all
    # below runs in ServerErrorMiddleware, which re-raises after responding, so
    # the server would print the raw database error (app/core/db_errors.py).
    for database_exception_type in DATABASE_EXCEPTION_TYPES:
        app.add_exception_handler(database_exception_type, database_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    # A database error after the response has started cannot be handled above;
    # keep it out of the server's log as well (app/core/db_errors.py).
    install_server_log_filter()
