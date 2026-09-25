"""Regression tests for alembic/env.py and percent-encoded DATABASE_URL values (F-11).

alembic/env.py hands the URL to Config.set_main_option(), which goes through
ConfigParser interpolation: an unescaped "%" (percent-encoded credentials such
as %40 or %25) raises ValueError -- whose message echoes the whole URL,
password included -- or would be mangled. env.py stores "%" as "%%", and these
tests execute the real env.py to prove SQLAlchemy still receives exactly the
normalized URL.

No database is contacted: the online path stops at a spy around
engine_from_config (create_engine is lazy) and the offline path only renders
SQL. Every credential below is a made-up canary, never a real secret.
"""
from __future__ import annotations

import io
import logging.config

import pytest
import sqlalchemy
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.core.config import BACKEND_DIR, Settings, get_settings
from app.core.db_safety import UnsafeConfigurationError
from app.db.base import normalize_database_url

# (id, DATABASE_URL as written in .env, expected decoded user, expected decoded password)
CASES = [
    pytest.param(
        "postgresql://canaryuser:canary%40pw@localhost:5432/artesanfc_test",
        "canaryuser",
        "canary@pw",
        id="percent-40",
    ),
    pytest.param(
        "postgresql://canaryuser:canary%25pw@localhost:5432/artesanfc_test",
        "canaryuser",
        "canary%pw",
        id="percent-25",
    ),
    pytest.param(
        "postgresql://canary%40user:c%40n%2Fa%3Ar%25y%3Fpw%23x@localhost:5432/artesanfc_test",
        "canary@user",
        "c@n/a:r%y?pw#x",
        id="multiple-sequences",
    ),
    pytest.param(
        "postgresql://canaryuser:a%25%25b%2540c@localhost:5432/artesanfc_test",
        "canaryuser",
        "a%%b%40c",
        id="encoded-percent-runs-not-double-decoded",
    ),
    pytest.param(
        "postgresql://canaryuser:canarypw@localhost:5432/artesanfc_test",
        "canaryuser",
        "canarypw",
        id="no-percent-unchanged",
    ),
]


class _StopBeforeConnect(Exception):
    """Raised by the spy so the online migration never opens a connection."""


@pytest.fixture()
def alembic_cfg(monkeypatch):
    # env.py calls fileConfig(alembic.ini), which would disable the loggers
    # other tests rely on; irrelevant here.
    monkeypatch.setattr(logging.config, "fileConfig", lambda *a, **k: None)
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    yield cfg
    get_settings.cache_clear()


def _capture_online(monkeypatch, cfg, database_url):
    """Run the real env.py in online mode; return what Alembic hands to
    engine_from_config and the URL of the Engine SQLAlchemy builds from it."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    captured = {}
    real_engine_from_config = sqlalchemy.engine_from_config

    def spy(configuration, *args, **kwargs):
        captured["section_url"] = configuration["sqlalchemy.url"]
        captured["engine"] = real_engine_from_config(configuration, *args, **kwargs)
        raise _StopBeforeConnect

    monkeypatch.setattr(sqlalchemy, "engine_from_config", spy)
    with pytest.raises(_StopBeforeConnect):
        command.current(cfg)
    return captured


@pytest.mark.parametrize("database_url, user, password", CASES)
def test_env_passes_normalized_url_to_sqlalchemy_unchanged(
    monkeypatch, alembic_cfg, database_url, user, password
):
    captured = _capture_online(monkeypatch, alembic_cfg, database_url)
    expected = normalize_database_url(database_url)

    # What Alembic/ConfigParser hands back keeps the single "%" SQLAlchemy expects.
    assert captured["section_url"] == expected

    # And SQLAlchemy parses the same URL, decoding the credentials correctly.
    url = captured["engine"].url
    assert url.render_as_string(hide_password=False) == expected
    assert url.drivername == "postgresql+psycopg"
    assert url.username == user
    assert url.password == password
    assert url.host == "localhost"
    assert url.database == "artesanfc_test"


@pytest.mark.parametrize("database_url, user, password", CASES)
def test_env_offline_mode_accepts_percent_encoded_url(
    monkeypatch, alembic_cfg, database_url, user, password, capfd
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    alembic_cfg.output_buffer = io.StringIO()

    command.upgrade(alembic_cfg, "head", sql=True)

    output = alembic_cfg.output_buffer.getvalue()
    assert "CREATE TABLE" in output
    captured = capfd.readouterr()
    for text in (output, captured.out, captured.err):
        assert password not in text
        assert database_url not in text


def test_unescaped_percent_would_fail_and_leak_the_url(alembic_cfg):
    """Documents the bug F-11 fixes: a raw percent-encoded URL is rejected by
    Config.set_main_option and the ValueError echoes the URL. env.py must
    therefore keep escaping "%" before calling it."""
    raw = "postgresql+psycopg://canaryuser:canary%40pw@localhost:5432/artesanfc_test"
    with pytest.raises(ValueError):
        alembic_cfg.set_main_option("sqlalchemy.url", raw)

    alembic_cfg.set_main_option("sqlalchemy.url", raw.replace("%", "%%"))
    assert alembic_cfg.get_main_option("sqlalchemy.url") == raw
    assert make_url(raw).password == "canary@pw"


@pytest.mark.parametrize("mode", ["online", "offline"])
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_env_fails_closed_without_a_database_url(monkeypatch, alembic_cfg, capfd, mode, blank):
    """No implicit database (issue #101): without DATABASE_URL, Alembic stops
    while loading settings, before any engine, connection or SQL is created,
    and prints nothing that echoes a value."""
    if blank is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        # No developer backend/.env may fill the value in.
        monkeypatch.setitem(Settings.model_config, "env_file", None)
    else:
        monkeypatch.setenv("DATABASE_URL", blank)
    get_settings.cache_clear()

    engines = []
    monkeypatch.setattr(sqlalchemy, "engine_from_config", lambda *a, **k: engines.append(1))
    alembic_cfg.output_buffer = io.StringIO()

    with pytest.raises(UnsafeConfigurationError, match="DATABASE_URL is not set"):
        command.upgrade(alembic_cfg, "head", sql=(mode == "offline"))

    assert engines == []
    assert alembic_cfg.output_buffer.getvalue() == ""
    captured = capfd.readouterr()
    assert "postgresql" not in captured.out + captured.err
