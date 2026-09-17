import psycopg

from app.core.config import get_settings


def check_database_connection() -> bool:
    settings = get_settings()
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:
        return False
