from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ArtesaNFC API"
    app_env: str = "local"
    debug: bool = False

    database_url: str = "postgresql://artesanfc:artesanfc@localhost:5432/artesanfc"

    # .env.example (and any .env copied from it) also carries the discrete
    # POSTGRES_USER/PASSWORD/DB/HOST/PORT vars consumed directly by
    # docker-compose.yml for the `db` service; Settings only needs the
    # already-assembled DATABASE_URL, so those extra keys must be ignored
    # here rather than rejected.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
