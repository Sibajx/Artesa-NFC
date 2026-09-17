from fastapi import Depends, FastAPI

from app.api.v1.router import router as api_v1_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.db.session import check_database_connection

settings = get_settings()

app = FastAPI(title=settings.app_name)
register_exception_handlers(app)
app.include_router(api_v1_router)


@app.get("/health")
def health(database_ok: bool = Depends(check_database_connection)) -> dict:
    return {
        "status": "ok",
        "database": "connected" if database_ok else "unavailable",
    }
