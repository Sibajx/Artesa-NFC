from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.v1.router import router as api_v1_router
from app.core.config import get_settings
from app.core.errors import error_response, register_exception_handlers
from app.db.session import check_database_connection

settings = get_settings()

_RESOLVE_PATH = "/api/v1/certificates/resolve"

# The only body certificates/resolve accepts is {"token": "<43 chars>"}
# (~60 bytes; ~270 bytes even at the schema's 256-character max_length). 1 KiB
# leaves headroom for formatting and nothing legitimate ever comes close.
_RESOLVE_MAX_BODY_BYTES = 1024


class ResolveBodySizeLimitMiddleware:
    """Rejects an oversized body on POST certificates/resolve with 413
    (docs/SECURITY.md section 5.5, docs/OPERATIONS.md). Uvicorn imposes no body
    limit and FastAPI reads the whole body into memory before validating it, so
    without this a single request could make the process buffer as much as
    the edge lets through.

    Content-Length is only a fast path: a request that declares too much is
    answered without reading any body. It is never trusted on its own - a
    request with no Content-Length (chunked) or a false one is counted as it
    arrives, and the moment the total passes the limit the 413 is sent and
    reading stops (the rest is never consumed). A body within the limit is
    buffered (at most the limit plus the last chunk) and replayed to the app
    unchanged.

    Scoped to that one method and path (with or without a trailing slash, as
    ResolveNoStoreMiddleware): GET, OPTIONS and every other endpoint pass
    straight through. The 413 is written through the ``send`` it is handed, so
    the CORS and no-store layers outside it still apply to it."""

    def __init__(self, app: ASGIApp, max_body_bytes: int = _RESOLVE_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") != _RESOLVE_PATH
        ):
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isascii() and declared.isdigit():
            if int(declared) > self.max_body_bytes:
                await error_response(413)(scope, receive, send)
                return

        buffered: list[Message] = []
        received = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break  # http.disconnect: the app gets it after the buffered chunks
            received += len(message.get("body", b""))
            if received > self.max_body_bytes:
                await error_response(413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            if buffered:
                return buffered.pop(0)
            return await receive()

        await self.app(scope, replay, send)


class ResolveNoStoreMiddleware:
    """Adds `Cache-Control: no-store` to every response for
    certificates/resolve (docs/SECURITY.md section 7) - 200, 422 and 405
    alike, since they all pass through here regardless of which handler
    produced them. Scoped to that one path on purpose: the public catalog
    GET endpoints are not credentials-bearing and keep their existing
    caching behavior."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].rstrip("/") != _RESOLVE_PATH:
            await self.app(scope, receive, send)
            return

        async def send_with_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_no_store)


# staging/production serve no interactive docs and no OpenAPI schema
# (Settings.docs_enabled). Passing None removes the routes altogether -
# /docs, /redoc, /openapi.json and /docs/oauth2-redirect become ordinary 404s.
_docs_urls: dict[str, None] = (
    {} if settings.docs_enabled else {"docs_url": None, "redoc_url": None, "openapi_url": None}
)
app = FastAPI(title=settings.app_name, **_docs_urls)
# Middleware order matters: the last one added is the outermost. The body
# limit goes first so it is innermost - its 413 then travels out through
# ResolveNoStoreMiddleware (Cache-Control: no-store) and CORSMiddleware (CORS
# headers for an allowed origin) like any other resolve response.
app.add_middleware(ResolveBodySizeLimitMiddleware)
# Added before CORSMiddleware so it sits inside it: CORS headers are still
# applied by the outer layer, and this only touches the resolve responses
# produced by the app itself.
app.add_middleware(ResolveNoStoreMiddleware)
# Explicit allowlist only (docs/SECURITY.md section 10) - no "*", no origin
# regex. GET and POST are the only methods the public API's browser
# integration needs: GET for the artisan/piece catalog, POST for
# certificates/resolve (API_CONTRACT.md section 3/7 - the frontend calls it
# from a browser). OPTIONS preflight is handled by the middleware itself
# regardless of allow_methods. No credentials: the public API has no
# cookie/session auth. This is an implementation of the already-approved API
# contract, not a broadening of CORS policy - the origin allowlist,
# allow_credentials, and allowed headers are unchanged.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_methods=["GET", "POST"],
    allow_credentials=False,
)
register_exception_handlers(app)
app.include_router(api_v1_router)


_HEALTH_HEADERS = {"Cache-Control": "no-store"}


# HEAD is answered by the same handler (same status, headers, no body on the
# wire) for monitors that probe with it; it stays out of the OpenAPI schema so
# the two methods do not share an operation id.
@app.head("/health", include_in_schema=False)
@app.get("/health")
def health(database_ok: bool = Depends(check_database_connection)) -> JSONResponse:
    """The API is only healthy if it can reach its database: 200 when it can,
    503 when it cannot. Bodies are fixed strings - nothing about the failure
    (host, user, driver message) is ever reported. Never cacheable. One
    endpoint on purpose (docs/OPERATIONS.md): do not wire it to an automatic
    restart, a database blip should not restart the process."""
    if database_ok:
        return JSONResponse({"status": "ok", "database": "connected"}, headers=_HEALTH_HEADERS)
    return JSONResponse(
        {"status": "unavailable", "database": "unavailable"},
        status_code=503,
        headers=_HEALTH_HEADERS,
    )
