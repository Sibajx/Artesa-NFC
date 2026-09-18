from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as api_v1_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.db.session import check_database_connection

settings = get_settings()

app = FastAPI(title=settings.app_name)
# Explicit allowlist only (docs/SECURITY.md section 10) - no "*", no origin
# regex. GET is the only method the public API's browser integration needs;
# OPTIONS preflight is handled by the middleware itself regardless of
# allow_methods. No credentials: the public API has no cookie/session auth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_methods=["GET"],
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
