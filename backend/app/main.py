from fastapi import Depends, FastAPI

from app.core.config import get_settings
from app.db.session import check_database_connection

settings = get_settings()

app = FastAPI(title=settings.app_name)


@app.get("/health")
def health(database_ok: bool = Depends(check_database_connection)) -> dict:
    return {
        "status": "ok",
        "database": "connected" if database_ok else "unavailable",
    }
