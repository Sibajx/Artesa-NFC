from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ArtesaNFC API"
    app_env: str = "local"
    debug: bool = False

    database_url: str = "postgresql://artesanfc:artesanfc@localhost:5432/artesanfc"

    # Explicit CORS allowlist (docs/SECURITY.md section 10: never "*", never
    # a permissive regex). Comma-separated so a plain .env value stays
    # human-editable, instead of requiring JSON-encoding a list in one env
    # var. Default covers local development only (VS Code Live Server, the
    # standard tool for this repo's build-step-free static frontend, per
    # docs/ARCHITECTURE.md section 9); production deployments must override
    # this with the real frontend origin, e.g.
    # CORS_ALLOWED_ORIGINS=https://artesanfc.com.
    cors_allowed_origins: str = "http://127.0.0.1:5500,http://localhost:5500"

    # .env.example (and any .env copied from it) also carries the discrete
    # POSTGRES_USER/PASSWORD/DB/HOST/PORT vars consumed directly by
    # docker-compose.yml for the `db` service; Settings only needs the
    # already-assembled DATABASE_URL, so those extra keys must be ignored
    # here rather than rejected.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
