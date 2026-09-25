"""Regression test for app/core/config.py::Settings.model_config extra="ignore".

.env.example (and any real .env copied from it) intentionally carries both
the app's DATABASE_URL and the discrete POSTGRES_USER/PASSWORD/DB/HOST/PORT
keys docker-compose.yml uses for the `db` service. Settings only declares
DATABASE_URL, so those extra keys must be tolerated, not rejected."""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path

import pytest
from pydantic import ValidationError

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


# DATABASE_URL has no default (issue #101), so every test starts from an
# explicit, obviously fake local one. Tests about a missing/blank value remove
# or blank it themselves.
LOCAL_DB = "postgresql://example:example@localhost:5432/example"
DB_NOT_SET = "DATABASE_URL is not set"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """The suite itself runs with APP_ENV=test and a real DATABASE_URL
    exported; start every test here from a blank slate, as APP_ENV=local with
    an explicit local DATABASE_URL."""
    for key in ("APP_ENV", "DATABASE_URL", "CORS_ALLOWED_ORIGINS", "DEBUG"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", LOCAL_DB)


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


def test_local_with_explicit_app_env_and_database_url_is_allowed():
    settings = Settings(_env_file=None)
    assert settings.app_env == "local"
    assert settings.database_url == LOCAL_DB
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


# --- DATABASE_URL is required: no implicit runtime default (issue #101) ---


def _no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_database_url_has_no_default_and_stays_out_of_repr():
    field = Settings.model_fields["database_url"]
    assert field.default == ""
    assert field.default != DEFAULT_DATABASE_URL
    assert field.repr is False


def test_database_url_unset_fails_closed(monkeypatch):
    _no_database_url(monkeypatch)
    with pytest.raises(UnsafeConfigurationError, match=DB_NOT_SET) as info:
        Settings(_env_file=None)
    # Not a ValueError: pydantic would wrap it in a ValidationError that echoes
    # the input (see db_safety.py).
    assert not isinstance(info.value, ValueError)
    assert_no_secrets(info.value)


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n", " \t\n "])
def test_database_url_blank_env_var_fails_closed(monkeypatch, blank):
    monkeypatch.setenv("DATABASE_URL", blank)
    with pytest.raises(UnsafeConfigurationError, match=DB_NOT_SET):
        Settings(_env_file=None)


@pytest.mark.parametrize("line", ["DATABASE_URL=", 'DATABASE_URL=""', 'DATABASE_URL="   "'])
def test_database_url_blank_in_env_file_fails_closed(tmp_path, monkeypatch, line):
    _no_database_url(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(f"APP_ENV=local\n{line}\n")
    with pytest.raises(UnsafeConfigurationError, match=DB_NOT_SET):
        Settings(_env_file=env_file)


@pytest.mark.parametrize("blank", ["", "   "])
def test_database_url_blank_init_value_fails_closed(blank):
    with pytest.raises(UnsafeConfigurationError, match=DB_NOT_SET):
        Settings(_env_file=None, database_url=blank)


@pytest.mark.parametrize("app_env", ["local", "test", "staging", "production"])
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_missing_database_url_fails_in_every_environment(monkeypatch, app_env, blank):
    _no_database_url(monkeypatch)
    # Everything else is valid for production, so DATABASE_URL is the only reason.
    kwargs = {"app_env": app_env, "cors_allowed_origins": PROD_ORIGIN, "debug": False}
    if blank is not None:
        kwargs["database_url"] = blank
    with pytest.raises(UnsafeConfigurationError, match=DB_NOT_SET):
        Settings(_env_file=None, **kwargs)


def test_app_env_is_validated_before_database_url(monkeypatch):
    _no_database_url(monkeypatch)
    monkeypatch.delenv("APP_ENV")
    with pytest.raises(UnsafeConfigurationError, match="APP_ENV is not set"):
        Settings(_env_file=None)
    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(UnsafeConfigurationError, match="unsupported value"):
        Settings(_env_file=None)


def test_database_url_is_validated_before_the_production_guards():
    # Missing URL wins over bad CORS and DEBUG, and over the staging/production
    # placeholder-password rule (which needs a URL to inspect).
    with pytest.raises(UnsafeConfigurationError, match=DB_NOT_SET):
        _settings(database_url="", cors_allowed_origins="http://localhost:5500", debug=True)


def test_missing_database_url_error_never_echoes_the_environment(tmp_path, monkeypatch):
    # Regression for the rejected "required field" design: pydantic's
    # "Field required" error prints the whole input dict, which includes the
    # dotenv-only POSTGRES_* keys (and would include DATABASE_URL if set).
    _no_database_url(monkeypatch)
    monkeypatch.setenv("UNRELATED_SHELL_SECRET", "ShellCanarySecret")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=local\n"
        f"POSTGRES_USER={CANARY_USER}\n"
        f"POSTGRES_PASSWORD={CANARY_PASSWORD}\n"
        "POSTGRES_DB=canary_db\n"
        "POSTGRES_HOST=canary.host.example\n"
    )
    with pytest.raises(UnsafeConfigurationError) as info:
        Settings(_env_file=env_file)
    assert_no_secrets(info.value, "canary_db", "canary.host.example", "ShellCanarySecret")
    text = str(info.value) + repr(info.value) + "".join(traceback.format_exception(info.value))
    for marker in ("input_value", "input_type", "validation error", "postgres_password"):
        assert marker not in text.lower(), marker


def test_explicit_local_database_url_still_works_including_the_dev_url(monkeypatch):
    # Explicit is fine in local (and only rejected by the staging/production
    # guard); what is gone is the *implicit* default.
    monkeypatch.setenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    assert Settings(_env_file=None).database_url == DEFAULT_DATABASE_URL


def test_database_url_value_is_stored_unmodified(monkeypatch):
    # The presence check only inspects the value; it must not strip or
    # re-encode it (percent-encoded credentials, F-11).
    raw = "postgresql://canaryuser:canary%40pw@localhost:5432/artesanfc_test"
    monkeypatch.setenv("DATABASE_URL", raw)
    assert Settings(_env_file=None).database_url == raw


def test_pydantic_validation_errors_hide_input_values():
    assert Settings.model_config["hide_input_in_errors"] is True
    url = f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@localhost:5432/artesanfc_test"
    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None, database_url=url, debug="notabool-canary")
    text = str(info.value)
    assert "input_value" not in text
    assert "notabool-canary" not in text
    assert_no_secrets(info.value)


def test_import_of_the_app_fails_closed_without_database_url():
    # What uvicorn/alembic/seed hit first: app.db.base builds the engine at
    # import time. A present-but-blank variable beats any backend/.env.
    env = {
        **os.environ,
        "PYTHONPATH": str(BACKEND_DIR),
        "APP_ENV": "local",
        "DATABASE_URL": "",
        "POSTGRES_PASSWORD": CANARY_PASSWORD,
    }
    result = subprocess.run(
        [sys.executable, "-c", "import app.db.base"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    assert f"UnsafeConfigurationError: {DB_NOT_SET}" in result.stderr
    assert CANARY_PASSWORD not in result.stdout + result.stderr
    assert "input_value" not in result.stderr


# --- deterministic .env resolution ---


def test_env_file_is_anchored_to_backend_dir_not_the_working_directory(tmp_path, monkeypatch):
    assert ENV_FILE == BACKEND_DIR / ".env"
    assert ENV_FILE.is_absolute()
    assert Settings.model_config["env_file"] == ENV_FILE

    stray = tmp_path / ".env"
    stray.write_text("DATABASE_URL=postgresql://stray:stray@stray.example:5432/stray_marker\n")
    monkeypatch.chdir(tmp_path)
    # A real environment variable would shadow the dotenv file below.
    monkeypatch.delenv("DATABASE_URL")

    # Positive control: the marker file *would* be picked up if it were read.
    assert "stray_marker" in Settings(_env_file=stray).database_url
    # ...but a cwd-relative ".env" is not consulted any more. DATABASE_URL has
    # no default, so without another source (a developer's own backend/.env)
    # this refuses to start instead of returning a value.
    try:
        value = Settings().database_url
    except UnsafeConfigurationError:
        value = ""
    assert "stray_marker" not in value


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
