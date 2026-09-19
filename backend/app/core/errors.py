from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.db_errors import DATABASE_EXCEPTION_TYPES, database_exception_handler

# API_CONTRACT.md section 10: standard public error envelope
# {"error": {"code": ..., "message": ...}}. Never leaks stack traces or
# database error text.

_DEFAULT_MESSAGES: dict[int, str] = {
    400: "The request could not be processed.",
    404: "The requested resource does not exist.",
    405: "This method is not allowed for this resource.",
    429: "Too many requests.",
    500: "An unexpected error occurred.",
}

_DEFAULT_CODES: dict[int, str] = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
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


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
    )


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
