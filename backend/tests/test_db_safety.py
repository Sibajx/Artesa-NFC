"""Tests for app/core/db_safety.py and the pytest session guard in
tests/conftest.py.

Everything here is pure (no database connection, no writes) except the
subprocess test, which deliberately points a child pytest at an unreachable
production-looking URL to prove the guard aborts before any database access.
"""
from __future__ import annotations

import os
import subprocess
import sys
import traceback

import pytest

from app.core.config import BACKEND_DIR
from app.core.db_safety import (
    DatabaseTarget,
    UnsafeConfigurationError,
    assert_database_url_configured,
    assert_safe_for_tests,
    is_test_database_name,
    normalize_app_env,
    parse_database_target,
)
from tests.safety_helpers import CANARY_PASSWORD, CANARY_USER, assert_no_secrets, credentialed_url

# --- database name convention ---


@pytest.mark.parametrize(
    "name",
    ["artesanfc_test", "test_artesanfc", "ci-test-db", "my_test_db", "test", "ARTESANFC_TEST"],
)
def test_names_with_a_delimited_test_token_are_test_databases(name):
    assert is_test_database_name(name)


@pytest.mark.parametrize(
    "name",
    ["artesanfc", "artesanfc_prod", "contest", "latest", "attestation", "testing", "artesanfc_tests", "", None],
)
def test_other_names_are_not_test_databases(name):
    assert not is_test_database_name(name)


# --- URL parsing ---


def test_parse_database_target_keeps_only_hosts_and_database():
    target = parse_database_target(credentialed_url("artesanfc_test", host="db.example"))
    assert target == DatabaseTarget(hosts=("db.example",), database="artesanfc_test")
    assert CANARY_PASSWORD not in repr(target)
    assert CANARY_USER not in repr(target)


def test_parse_database_target_sees_libpq_host_query_parameters():
    target = parse_database_target(f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@/artesanfc?host=10.0.0.5")
    assert target.hosts == ("10.0.0.5",)


def test_parse_database_target_error_does_not_echo_the_input():
    with pytest.raises(UnsafeConfigurationError) as info:
        parse_database_target(f"definitely not a url {CANARY_PASSWORD}")
    assert CANARY_PASSWORD not in "".join(traceback.format_exception(info.value))


def test_normalize_app_env_requires_an_explicit_known_value():
    assert normalize_app_env(" Staging ") == "staging"
    for bad in (None, "", "  ", "dev", "development", "prod"):
        with pytest.raises(UnsafeConfigurationError):
            normalize_app_env(bad)


# --- DATABASE_URL presence (no implicit default, issue #101) ---


@pytest.mark.parametrize("value", [None, "", " ", "   ", "\t", "\n", " \t\n "])
def test_unset_or_blank_database_url_is_rejected(value):
    with pytest.raises(UnsafeConfigurationError, match="DATABASE_URL is not set") as info:
        assert_database_url_configured(value)
    assert not isinstance(info.value, ValueError)
    assert_no_secrets(info.value)
    assert info.value.__cause__ is None


@pytest.mark.parametrize(
    "value",
    [credentialed_url("artesanfc_test"), credentialed_url("artesanfc", host="db"), "not even a url", "sqlite://"],
)
def test_any_non_blank_value_passes_the_presence_check(value):
    # Presence only: format and target rules live in the other guards.
    assert assert_database_url_configured(value) is None


def test_presence_error_is_static_and_never_echoes_the_value():
    with pytest.raises(UnsafeConfigurationError) as info:
        assert_database_url_configured("\t  \n")
    assert str(info.value) == (
        "DATABASE_URL is not set. Set it explicitly (environment variable or "
        "backend/.env); there is no default database."
    )


# --- test-database guard ---


def test_test_env_with_test_database_is_accepted():
    assert_safe_for_tests("test", credentialed_url("artesanfc_test"))
    assert_safe_for_tests(" TEST ", credentialed_url("test_artesanfc", host="ci-postgres.example"))


@pytest.mark.parametrize("database", ["artesanfc", "artesanfc_prod", "contest"])
def test_non_test_database_is_rejected(database):
    with pytest.raises(UnsafeConfigurationError, match="not marked as a test database"):
        assert_safe_for_tests("test", credentialed_url(database))


def test_localhost_non_test_database_is_rejected_without_leaking_credentials():
    with pytest.raises(UnsafeConfigurationError) as info:
        assert_safe_for_tests("test", credentialed_url("artesanfc", host="localhost"))
    assert_no_secrets(info.value, "localhost")
    assert "No database was touched" in str(info.value)


@pytest.mark.parametrize("app_env", ["production", "staging", "local", "", None, "prod"])
def test_any_env_other_than_test_is_rejected_even_for_a_test_database(app_env):
    with pytest.raises(UnsafeConfigurationError, match="requires APP_ENV=test") as info:
        assert_safe_for_tests(app_env, credentialed_url("artesanfc_test"))
    assert_no_secrets(info.value, "localhost")


def test_unparseable_url_is_rejected_generically():
    with pytest.raises(UnsafeConfigurationError) as info:
        assert_safe_for_tests("test", f"nonsense {CANARY_PASSWORD}")
    assert CANARY_PASSWORD not in "".join(traceback.format_exception(info.value))


# --- the pytest session guard itself (child pytest process) ---


def _run_child_pytest(**env_overrides) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "DATABASE_URL")}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_health.py", "-q", "-p", "no:cacheprovider"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_pytest_session_aborts_before_any_test_for_a_non_test_database():
    # Port 1 is unreachable: if the guard did not abort first, the suite
    # would fail differently (connection error) rather than refuse.
    result = _run_child_pytest(
        APP_ENV="test",
        DATABASE_URL=f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@127.0.0.1:1/artesanfc_prod",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "Refusing to run tests" in output
    assert "No database was touched" in output
    assert "passed" not in output
    assert CANARY_PASSWORD not in output
    assert CANARY_USER not in output


def test_pytest_session_aborts_when_app_env_is_not_test():
    result = _run_child_pytest(
        APP_ENV="production",
        DATABASE_URL=f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@127.0.0.1:1/artesanfc_test",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert CANARY_PASSWORD not in output
    assert "passed" not in output


def test_pytest_session_aborts_when_app_env_is_unset():
    result = _run_child_pytest(
        DATABASE_URL=f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@127.0.0.1:1/artesanfc_test",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "APP_ENV is not set" in output
    assert CANARY_PASSWORD not in output


@pytest.mark.parametrize("blank", ["", "   "])
def test_pytest_session_aborts_when_database_url_is_missing(blank):
    # Present-but-blank (not absent) so a developer's own backend/.env cannot
    # fill the value in and mask what is being tested.
    result = _run_child_pytest(APP_ENV="test", DATABASE_URL=blank)
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "DATABASE_URL is not set" in output
    assert "passed" not in output
