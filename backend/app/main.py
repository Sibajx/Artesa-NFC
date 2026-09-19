from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.v1.router import router as api_v1_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.db.session import check_database_connection

settings = get_settings()

_RESOLVE_PATH = "/api/v1/certificates/resolve"


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


app = FastAPI(title=settings.app_name)
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


@app.get("/health")
def health(database_ok: bool = Depends(check_database_connection)) -> dict:
    return {
        "status": "ok",
        "database": "connected" if database_ok else "unavailable",
    }
