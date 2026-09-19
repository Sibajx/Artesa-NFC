from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


settings = get_settings()
# hide_parameters=True keeps bound parameter values (e.g. the token_hash
# looked up by certificates/resolve) out of SQLAlchemy's DBAPIError text and
# statement logging (docs/SECURITY.md sections 12.2 and 13).
engine = create_engine(
    normalize_database_url(settings.database_url), hide_parameters=True
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
