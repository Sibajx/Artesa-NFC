"""Regression test for app/core/config.py::Settings.model_config extra="ignore".

.env.example (and any real .env copied from it) intentionally carries both
the app's DATABASE_URL and the discrete POSTGRES_USER/PASSWORD/DB/HOST/PORT
keys docker-compose.yml uses for the `db` service. Settings only declares
DATABASE_URL, so those extra keys must be tolerated, not rejected."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import BACKEND_DIR, ENV_FILE, Settings
from app.core.db_safety import DEFAULT_DATABASE_URL, UnsafeConfigurationError
from tests.safety_helpers import CANARY_PASSWORD, CANARY_USER, assert_no_secrets

_DOTENV_ONLY_KEYS = (
    "APP_ENV",
    "DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """The suite itself runs with APP_ENV=test and a real DATABASE_URL
    exported; start every test here from a blank slate, as APP_ENV=local."""
    for key in ("APP_ENV", "DATABASE_URL", "CORS_ALLOWED_ORIGINS", "DEBUG"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_ENV", "local")


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
        "APP_ENV=local\n"
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


# --- APP_ENV model and the production configuration guard ---
# (app/core/config.py::Settings._validate_environment, app/core/db_safety.py)

PROD_DB = f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@db.internal.example:5432/artesanfc_prod"
PROD_ORIGIN = "https://artesanfc.com"


def _settings(**overrides) -> Settings:
    kwargs = {
        "app_env": "production",
        "database_url": PROD_DB,
        "cors_allowed_origins": PROD_ORIGIN,
        "debug": False,
    }
    kwargs.update(overrides)
    return Settings(_env_file=None, **kwargs)


def test_local_defaults_are_allowed_with_explicit_app_env():
    settings = Settings(_env_file=None)
    assert settings.app_env == "local"
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.cors_allowed_origins_list == [
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ]


def test_app_env_unset_fails_clearly(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(UnsafeConfigurationError, match="APP_ENV is not set"):
        Settings(_env_file=None)


def test_app_env_empty_fails_clearly(monkeypatch):
    monkeypatch.setenv("APP_ENV", "   ")
    with pytest.raises(UnsafeConfigurationError, match="APP_ENV is not set"):
        Settings(_env_file=None)


@pytest.mark.parametrize("alias", ["dev", "development", "prod", "productoin", "stage", "Local1"])
def test_unknown_app_env_aliases_are_rejected_not_mapped(monkeypatch, alias):
    monkeypatch.setenv("APP_ENV", alias)
    with pytest.raises(UnsafeConfigurationError, match="unsupported value"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("local", "local"), ("  LOCAL ", "local"), ("Test", "test")],
)
def test_app_env_is_trimmed_and_lowercased(monkeypatch, raw, expected):
    monkeypatch.setenv("APP_ENV", raw)
    assert Settings(_env_file=None).app_env == expected


def test_app_env_is_normalized_before_the_production_guard():
    # "Production " must not bypass the guard by differing in case/space.
    with pytest.raises(UnsafeConfigurationError, match="CORS_ALLOWED_ORIGINS"):
        _settings(app_env=" Production ", cors_allowed_origins="http://localhost:5500")


def test_production_with_explicit_safe_values_is_accepted():
    settings = _settings()
    assert settings.app_env == "production"


def test_production_rejects_the_development_default_database():
    with pytest.raises(UnsafeConfigurationError, match="development default"):
        _settings(database_url=DEFAULT_DATABASE_URL)


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql://{CANARY_USER}:change-me@db:5432/artesanfc",  # .env.example
        f"postgresql://{CANARY_USER}:artesanfc@db:5432/artesanfc",  # docker-compose default
        f"postgresql://{CANARY_USER}@db.internal.example:5432/artesanfc",  # no password
    ],
)
def test_production_rejects_placeholder_or_missing_database_password(url):
    with pytest.raises(UnsafeConfigurationError, match="placeholder password") as info:
        _settings(database_url=url)
    assert_no_secrets(info.value, "db.internal.example")


def test_production_rejects_unparseable_database_url_without_echoing_it():
    with pytest.raises(UnsafeConfigurationError) as info:
        _settings(database_url=f"not a url {CANARY_PASSWORD}")
    assert_no_secrets(info.value, "db.internal.example")


@pytest.mark.parametrize(
    "origins",
    [
        "http://127.0.0.1:5500,http://localhost:5500",  # the dev default alone
        "https://www.artesanfc.com",
        "https://artesanfc.com/",  # trailing slash never matches a browser Origin
        "http://artesanfc.com",
        "",
    ],
)
def test_production_requires_the_artesanfc_origin_explicitly(origins):
    with pytest.raises(UnsafeConfigurationError, match="https://artesanfc.com") as info:
        _settings(cors_allowed_origins=origins)
    assert_no_secrets(info.value, "db.internal.example")


def test_production_rejects_debug_true():
    with pytest.raises(UnsafeConfigurationError, match="DEBUG must be false"):
        _settings(debug=True)


def test_staging_rejects_default_database_but_needs_no_production_origin():
    with pytest.raises(UnsafeConfigurationError, match="development default"):
        _settings(app_env="staging", database_url=DEFAULT_DATABASE_URL)
    settings = _settings(app_env="staging", cors_allowed_origins="https://staging.example")
    assert settings.app_env == "staging"


def test_config_errors_and_repr_never_contain_credentials():
    # Failure that occurs *after* a credentialed URL was accepted as input:
    # pydantic would echo input_value for a ValueError; ours must not.
    with pytest.raises(UnsafeConfigurationError) as info:
        _settings(cors_allowed_origins="http://localhost:5500")
    assert not isinstance(info.value, ValueError)
    assert_no_secrets(info.value, "db.internal.example")

    settings = _settings()
    assert CANARY_PASSWORD not in repr(settings)
    assert CANARY_USER not in repr(settings)
    assert CANARY_PASSWORD not in str(settings)


# --- deterministic .env resolution ---


def test_env_file_is_anchored_to_backend_dir_not_the_working_directory(tmp_path, monkeypatch):
    assert ENV_FILE == BACKEND_DIR / ".env"
    assert ENV_FILE.is_absolute()
    assert Settings.model_config["env_file"] == ENV_FILE

    stray = tmp_path / ".env"
    stray.write_text("DATABASE_URL=postgresql://stray:stray@stray.example:5432/stray_marker\n")
    monkeypatch.chdir(tmp_path)

    # Positive control: the marker file *would* be picked up if it were read.
    assert "stray_marker" in Settings(_env_file=stray).database_url
    # ...but a cwd-relative ".env" is not consulted any more.
    assert "stray_marker" not in Settings().database_url


def test_env_file_path_is_identical_from_any_working_directory():
    code = "from app.core.config import ENV_FILE; print(ENV_FILE)"
    env = {**os.environ, "PYTHONPATH": str(BACKEND_DIR)}
    outputs = {
        subprocess.run(
            [sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, check=True
        ).stdout.strip()
        for cwd in (BACKEND_DIR, BACKEND_DIR.parent, Path("/"))
    }
    assert outputs == {str(BACKEND_DIR / ".env")}


def test_env_file_is_optional(tmp_path):
    assert Settings(_env_file=tmp_path / "does-not-exist.env").app_env == "local"


def test_environment_variables_override_env_file_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=production\nDEBUG=true\n")
    monkeypatch.setenv("APP_ENV", "local")
    settings = Settings(_env_file=env_file)
    assert settings.app_env == "local"


def test_app_env_can_come_from_the_env_file_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=local\n")
    assert Settings(_env_file=env_file).app_env == "local"
