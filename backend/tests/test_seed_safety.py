"""Seed safety policy (app/core/db_safety.py::assert_safe_for_seed and the
guard at the top of app/db/seed.py::main).

Kept separate from test_seed.py, whose autouse fixture re-seeds the database
after every test; the guard tests here need no database at all."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.db_safety import UnsafeConfigurationError, assert_safe_for_seed
from app.db import seed as seed_module
from tests.safety_helpers import CANARY_PASSWORD, CANARY_USER, assert_no_secrets, credentialed_url


def _url(database: str = "artesanfc", host: str = "localhost") -> str:
    return credentialed_url(database, host)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "db", "LOCALHOST"])
def test_local_env_with_local_host_is_accepted(host):
    assert_safe_for_seed("local", _url(host=f"[{host}]" if host == "::1" else host))


def test_local_env_with_unix_socket_is_accepted():
    assert_safe_for_seed("local", f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@/artesanfc?host=/var/run/postgresql")


def test_test_env_with_test_database_is_accepted():
    assert_safe_for_seed("test", _url("artesanfc_test", host="ci-postgres.remote.example"))


def test_production_is_rejected():
    with pytest.raises(UnsafeConfigurationError, match="only allowed with APP_ENV=local or APP_ENV=test") as info:
        assert_safe_for_seed("production", _url())
    assert_no_secrets(info.value, "remote.example")


@pytest.mark.parametrize("app_env", ["staging", "prod", "development", "", None])
def test_other_envs_are_rejected(app_env):
    with pytest.raises(UnsafeConfigurationError):
        assert_safe_for_seed(app_env, _url())


def test_local_env_with_remote_host_is_rejected():
    with pytest.raises(UnsafeConfigurationError, match="local development host") as info:
        assert_safe_for_seed("local", _url(host="remote.example"))
    assert_no_secrets(info.value, "remote.example")


def test_local_env_with_remote_host_hidden_in_query_parameter_is_rejected():
    url = f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@/artesanfc?host=remote.example"
    with pytest.raises(UnsafeConfigurationError, match="local development host") as info:
        assert_safe_for_seed("local", url)
    assert_no_secrets(info.value, "remote.example")


def test_local_env_with_a_mix_of_local_and_remote_hosts_is_rejected():
    url = f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@localhost/artesanfc?host=localhost,remote.example"
    with pytest.raises(UnsafeConfigurationError):
        assert_safe_for_seed("local", url)


def test_test_env_with_non_test_database_is_rejected():
    with pytest.raises(UnsafeConfigurationError, match="marked as a test database") as info:
        assert_safe_for_seed("test", _url("artesanfc"))
    assert_no_secrets(info.value, "remote.example")


# --- main(): refuses before any session or connection is created ---


def _fail_if_called(*args, **kwargs):
    raise AssertionError("SessionLocal must not be used when the seed guard refuses")


@pytest.mark.parametrize(
    ("app_env", "url"),
    [
        ("production", _url()),
        ("local", _url(host="remote.example")),
        ("test", _url("artesanfc")),
        ("staging", _url()),
    ],
)
def test_main_refuses_before_opening_any_session(monkeypatch, capsys, app_env, url):
    monkeypatch.setattr(
        seed_module, "get_settings", lambda: SimpleNamespace(app_env=app_env, database_url=url)
    )
    monkeypatch.setattr(seed_module, "SessionLocal", _fail_if_called)

    with pytest.raises(SystemExit) as info:
        seed_module.main()

    assert info.value.code == 1
    captured = capsys.readouterr()
    assert "Refusing to seed" in captured.err
    assert CANARY_PASSWORD not in captured.out + captured.err
    assert CANARY_USER not in captured.out + captured.err


def test_main_runs_against_the_guarded_test_database(capsys):
    # The suite only starts against APP_ENV=test + a test-marked database
    # (tests/conftest.py), which the seed guard accepts.
    seed_module.main()
    assert "Seed complete" in capsys.readouterr().out
