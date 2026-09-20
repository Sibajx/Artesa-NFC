"""Provisioning safety policy (app/core/db_safety.py::assert_safe_for_provisioning
and the guard at the top of app/cli/provision.py::run), issue N-09.

Needs no provisioning data: every guard here fires before any session opens."""
from __future__ import annotations

import subprocess
import sys

import pytest

from app.cli import provision as cli
from app.core.db_safety import (
    UnsafeConfigurationError,
    assert_safe_for_provisioning,
    assert_safe_for_seed,
    is_local_database_target,
)
from app.services import provisioning as prov
from tests.provisioning_helpers import BACKEND_DIR, ScriptedTerminal
from tests.safety_helpers import CANARY_PASSWORD, CANARY_USER, assert_no_secrets, credentialed_url


def _url(database: str = "artesanfc", host: str = "localhost") -> str:
    return credentialed_url(database, host)


# --- assert_safe_for_provisioning ---------------------------------------------------------


def test_production_with_a_real_database_is_accepted():
    assert_safe_for_provisioning("production", _url(host="db.internal.example"))


def test_production_is_still_refused_by_the_seed_guard():
    # The whole point of a separate guard: provisioning may touch production,
    # the demo seed may never.
    assert_safe_for_provisioning("production", _url())
    with pytest.raises(UnsafeConfigurationError):
        assert_safe_for_seed("production", _url())


@pytest.mark.parametrize("password", ["artesanfc", "change-me", ""])
def test_production_with_a_placeholder_password_is_refused(password):
    url = f"postgresql://svc:{password}@localhost:5432/artesanfc"
    with pytest.raises(UnsafeConfigurationError):
        assert_safe_for_provisioning("production", url)


@pytest.mark.parametrize("app_env", ["staging", "prod", "development", "dev", "", None, "PRODUCTIONS"])
def test_other_environments_are_refused(app_env):
    with pytest.raises(UnsafeConfigurationError, match="Provisioning rechazado") as info:
        assert_safe_for_provisioning(app_env, _url())
    assert_no_secrets(info.value, "remote.example")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "db"])
def test_local_with_a_local_host_is_accepted(host):
    assert_safe_for_provisioning("local", _url(host=host))


def test_local_with_a_remote_or_mixed_host_is_refused():
    with pytest.raises(UnsafeConfigurationError, match="host de la base de datos debe ser local") as info:
        assert_safe_for_provisioning("local", _url(host="remote.example"))
    assert_no_secrets(info.value, "remote.example")

    mixed = f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@localhost/artesanfc?host=localhost,remote.example"
    with pytest.raises(UnsafeConfigurationError):
        assert_safe_for_provisioning("local", mixed)


def test_test_env_needs_a_test_marked_database():
    assert_safe_for_provisioning("test", _url("artesanfc_test", host="ci-postgres.remote.example"))
    with pytest.raises(UnsafeConfigurationError, match="marcada como de prueba") as info:
        assert_safe_for_provisioning("test", _url("artesanfc"))
    assert_no_secrets(info.value, "remote.example")


def test_is_local_database_target_never_returns_the_host():
    assert is_local_database_target(_url(host="localhost")) is True
    assert is_local_database_target(_url(host="remote.example")) is False


# --- run(): refuses before any session or connection ---------------------------------------------


def _fail_if_called(*args, **kwargs):
    raise AssertionError("no session may be opened when the provisioning guard refuses")


@pytest.mark.parametrize(
    ("app_env", "url"),
    [
        ("staging", _url()),
        ("prod", _url()),
        ("local", _url(host="remote.example")),
        ("test", _url("artesanfc")),
        ("production", "postgresql://svc:change-me@localhost/artesanfc"),
    ],
)
@pytest.mark.parametrize("command", [["list"], ["status", "--piece", "X"], ["issue", "--piece", "X"], ["revoke", "--piece", "X", "--dry-run"]])
def test_run_refuses_before_opening_any_session(app_env, url, command):
    term = ScriptedTerminal()
    code = cli.run(command, terminal=term, session_factory=_fail_if_called, environment=(app_env, url))

    assert code == cli.EXIT_POLICY
    assert term.prompts == []
    text = term.visible_text
    assert "Configuración rechazada" in text
    for secret in (CANARY_PASSWORD, CANARY_USER, "postgresql://", "remote.example", "change-me"):
        assert secret not in text


def test_a_broken_settings_load_is_exit_2_and_names_only_the_type(monkeypatch):
    def boom():
        raise RuntimeError(f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@host/db")

    monkeypatch.setattr(cli, "_load_environment", boom)
    term = ScriptedTerminal()

    code = cli.run(["list"], terminal=term, session_factory=_fail_if_called)

    assert code == cli.EXIT_POLICY
    assert "RuntimeError" in term.visible_text
    assert CANARY_PASSWORD not in term.visible_text


def test_an_unsafe_configuration_error_from_settings_is_exit_2(monkeypatch):
    def refuse():
        raise UnsafeConfigurationError("APP_ENV is not set.")

    monkeypatch.setattr(cli, "_load_environment", refuse)
    term = ScriptedTerminal()
    assert cli.run(["list"], terminal=term, session_factory=_fail_if_called) == cli.EXIT_POLICY


# --- secret-showing commands need a real terminal -----------------------------------------------------


@pytest.mark.parametrize("command", [["issue", "--piece", "X"], ["rotate", "--piece", "X"]])
def test_secret_showing_commands_refuse_without_a_tty_before_any_query(command):
    term = ScriptedTerminal(secret_ok=False)

    code = cli.run(command, terminal=term, session_factory=_fail_if_called, environment=("test", _url("artesanfc_test")))

    assert code == cli.EXIT_POLICY
    assert term.prompts == [] and term.revealed == []
    assert "terminal interactivo" in term.visible_text


def test_a_real_non_tty_process_is_refused_and_prints_no_url():
    # No mock: a subprocess with stdin/stdout/stderr that are not TTYs.
    completed = subprocess.run(
        [sys.executable, "-m", "app.cli.provision", "issue", "--piece", "ANY"],
        cwd=BACKEND_DIR,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == cli.EXIT_POLICY
    assert "/c/" not in completed.stdout + completed.stderr
    assert "terminal interactivo" in completed.stderr


def test_console_terminal_requires_every_stream_to_be_a_tty(monkeypatch):
    class Stream:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            return self._tty

    monkeypatch.setenv("TERM", "xterm-256color")
    for streams, expected in [
        ((True, True, True), True),
        ((False, True, True), False),
        ((True, False, True), False),
        ((True, True, False), False),
    ]:
        monkeypatch.setattr(sys, "stdin", Stream(streams[0]))
        monkeypatch.setattr(sys, "stdout", Stream(streams[1]))
        monkeypatch.setattr(sys, "stderr", Stream(streams[2]))
        assert cli.ConsoleTerminal().secret_channel_ok() is expected

    monkeypatch.setattr(sys, "stdin", Stream(True))
    monkeypatch.setattr(sys, "stdout", Stream(True))
    monkeypatch.setattr(sys, "stderr", Stream(True))
    for term_value in ("dumb", ""):
        monkeypatch.setenv("TERM", term_value)
        assert cli.ConsoleTerminal().secret_channel_ok() is False


# --- the certificate URL --------------------------------------------------------------------------------


def _token() -> str:
    from app.services.certificates import generate_certificate_token

    return generate_certificate_token()


def test_production_url_uses_the_frontend_host_never_the_api_host():
    token = _token()
    url = prov.build_certificate_url("production", token)
    assert url == f"https://artesanfc.com/c/{token}"
    assert "api." not in url
    assert len(token) == 43


@pytest.mark.parametrize("app_env", ["local", "test"])
def test_local_and_test_urls_are_a_marked_rehearsal_never_the_real_host(app_env):
    url = prov.build_certificate_url(app_env, _token())
    assert url.startswith("http://127.0.0.1:5500/c/")
    assert "artesanfc.com" not in url
    assert prov.is_rehearsal_environment(app_env)
    assert not prov.is_rehearsal_environment("production")


@pytest.mark.parametrize("bad", ["", "short", "A" * 42, "A" * 44, "A" * 42 + "=", "A" * 42 + "!", None, 123])
def test_a_malformed_token_never_becomes_a_url_and_is_never_echoed(bad):
    with pytest.raises(ValueError) as info:
        prov.build_certificate_url("production", bad)  # type: ignore[arg-type]
    if isinstance(bad, str) and bad:
        assert bad not in str(info.value)


# --- the parser can not receive a secret ----------------------------------------------------------------


def _all_options(parser):
    options = set()
    for action in parser._actions:
        options.update(action.option_strings)
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            for sub in action.choices.values():
                options |= _all_options(sub)
    return options


def test_no_option_can_carry_a_token():
    options = _all_options(cli.build_parser())
    assert options == {"-h", "--help", "--piece", "--dry-run"}
    assert not any("token" in o or "url" in o or "secret" in o for o in options)


@pytest.mark.parametrize("flag", ["--token", "--url", "--secret", "--stdin", "--from-file", "--token-file"])
def test_secret_flags_are_rejected_as_a_usage_error(flag):
    term = ScriptedTerminal()
    code = cli.run(["issue", "--piece", "X", flag, "AAAA"], terminal=term, session_factory=_fail_if_called, environment=("test", _url("artesanfc_test")))
    assert code == cli.EXIT_PRECONDITION
    assert "AAAA" not in term.visible_text  # a rejected value is not echoed back
