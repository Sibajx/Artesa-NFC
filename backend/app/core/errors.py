from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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
    message = detail if isinstance(detail, str) else _DEFAULT_MESSAGES.get(
        status_code, "An error occurred."
    )
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
    app.add_exception_handler(Exception, unhandled_exception_handler)
