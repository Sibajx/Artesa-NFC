"""Regression test for app/core/config.py::Settings.model_config extra="ignore".

.env.example (and any real .env copied from it) intentionally carries both
the app's DATABASE_URL and the discrete POSTGRES_USER/PASSWORD/DB/HOST/PORT
keys docker-compose.yml uses for the `db` service. Settings only declares
DATABASE_URL, so those extra keys must be tolerated, not rejected."""
from __future__ import annotations

from app.core.config import Settings

_DOTENV_ONLY_KEYS = (
    "DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
)


def test_settings_ignores_docker_compose_only_postgres_keys(tmp_path, monkeypatch):
    # Real OS environment variables take priority over `_env_file` in
    # pydantic-settings, so a DATABASE_URL already exported in the shell
    # (e.g. by other tests/tooling in this session) would otherwise shadow
    # the temp dotenv file below and make this test's assertion meaningless
    # regardless of which file Settings actually read.
    for key in _DOTENV_ONLY_KEYS:
        monkeypatch.delenv(key, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql://example:example@localhost:5432/example\n"
        "POSTGRES_USER=example\n"
        "POSTGRES_PASSWORD=example\n"
        "POSTGRES_DB=example\n"
        "POSTGRES_HOST=localhost\n"
        "POSTGRES_PORT=5432\n"
    )

    # Construction succeeding at all (no ValidationError) is the point of
    # this test: the extra POSTGRES_* keys above previously made this raise.
    settings = Settings(_env_file=env_file)

    assert settings.database_url == "postgresql://example:example@localhost:5432/example"


# --- CORS allowlist parsing (app/main.py::CORSMiddleware consumes this) ---


def test_cors_allowed_origins_default_is_local_dev_only(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.cors_allowed_origins_list == [
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ]


def test_cors_allowed_origins_parses_comma_separated_env_value(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://one.example, https://two.example")
    settings = Settings(_env_file=None)
    assert settings.cors_allowed_origins_list == [
        "https://one.example",
        "https://two.example",
    ]


def test_cors_allowed_origins_supports_production_domain(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://artesanfc.com")
    settings = Settings(_env_file=None)
    assert settings.cors_allowed_origins_list == ["https://artesanfc.com"]


def test_cors_allowed_origins_empty_value_yields_empty_list(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")
    settings = Settings(_env_file=None)
    assert settings.cors_allowed_origins_list == []
