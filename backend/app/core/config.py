from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.db_safety import (
    ENV_LOCAL,
    ENV_PRODUCTION,
    ENV_STAGING,
    ENV_TEST,
    UnsafeConfigurationError,
    assert_database_url_configured,
    assert_production_grade_database,
    normalize_app_env,
)

# Anchored to this file (backend/app/core/config.py -> backend/), not to the
# process working directory: starting uvicorn/alembic/pytest from another
# directory must not change which .env is read (or silently read none and
# fall back to development defaults). The file is optional; real environment
# variables always take priority over it.
BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_DIR / ".env"

# The one browser origin production must allow (docs/SECURITY.md section 10).
PRODUCTION_FRONTEND_ORIGIN = "https://artesanfc.com"


class Settings(BaseSettings):
    app_name: str = "ArtesaNFC API"

    # Required, no default: one of local | test | staging | production (see
    # app/core/db_safety.py). An unset APP_ENV must fail loudly instead of
    # silently behaving like local development. Declared as a plain str with
    # an empty default (not a required field) on purpose: pydantic's
    # "field required" error would echo the whole input, DATABASE_URL and its
    # password included. The check lives in _validate_environment below.
    app_env: str = ""
    debug: bool = False

    # Required, with no default database: an unset, empty or whitespace-only
    # DATABASE_URL fails in _validate_environment below (after APP_ENV). Same
    # reason as app_env: a required pydantic field would produce a "field
    # required" ValidationError that echoes the whole input (the dotenv
    # POSTGRES_* keys, and DATABASE_URL when set). The empty default is never
    # used as a value.
    # repr=False keeps the URL (and its password) out of repr(settings),
    # tracebacks and pytest failure output.
    database_url: str = Field(default="", repr=False)

    # Explicit CORS allowlist (docs/SECURITY.md section 10: never "*", never
    # a permissive regex). Comma-separated so a plain .env value stays
    # human-editable, instead of requiring JSON-encoding a list in one env
    # var. Default covers local development only (VS Code Live Server, the
    # standard tool for this repo's build-step-free static frontend, per
    # docs/ARCHITECTURE.md section 9); APP_ENV=production refuses to start
    # unless this contains https://artesanfc.com, e.g.
    # CORS_ALLOWED_ORIGINS=https://artesanfc.com.
    cors_allowed_origins: str = "http://127.0.0.1:5500,http://localhost:5500"

    # .env.example (and any .env copied from it) also carries the discrete
    # POSTGRES_USER/PASSWORD/DB/HOST/PORT vars consumed directly by
    # docker-compose.yml for the `db` service; Settings only needs the
    # already-assembled DATABASE_URL, so those extra keys must be ignored
    # here rather than rejected.
    # hide_input_in_errors: belt and braces so no ValidationError (e.g. a bad
    # DEBUG value) ever prints input_value/input_type. Note errors() still
    # carries the input; nothing here logs it.
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def _validate_environment(self) -> "Settings":
        # Every failure here raises UnsafeConfigurationError (a RuntimeError,
        # not a ValueError) so pydantic does not wrap it in a ValidationError
        # that echoes input_value -- i.e. DATABASE_URL with its password.
        self.app_env = normalize_app_env(self.app_env)
        # After APP_ENV (which says what we are starting as), before the
        # staging/production guards (which need a URL to inspect).
        assert_database_url_configured(self.database_url)

        if self.app_env in (ENV_STAGING, ENV_PRODUCTION):
            assert_production_grade_database(self.database_url, self.app_env)

        if self.app_env == ENV_PRODUCTION:
            if PRODUCTION_FRONTEND_ORIGIN not in self.cors_allowed_origins_list:
                raise UnsafeConfigurationError(
                    "Refusing to start with APP_ENV=production: "
                    f"CORS_ALLOWED_ORIGINS must include {PRODUCTION_FRONTEND_ORIGIN}."
                )
            if self.debug:
                raise UnsafeConfigurationError(
                    "Refusing to start with APP_ENV=production: DEBUG must be "
                    "false (docs/SECURITY.md)."
                )
        return self

    @property
    def docs_enabled(self) -> bool:
        """Interactive docs (/docs, /redoc, /docs/oauth2-redirect) and the
        OpenAPI schema (/openapi.json) are a local/test convenience only:
        staging and production do not serve them at all (docs/SECURITY.md
        section 13, docs/OPERATIONS.md). Deciding it here, in the app, keeps
        it independent of whatever sits in front of the API."""
        return self.app_env in (ENV_LOCAL, ENV_TEST)

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
