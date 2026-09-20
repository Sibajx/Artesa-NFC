"""The raw token must appear ONLY in the CLI's reveal channel (issue N-09).

A fixed canary stands in for the token so every leak assertion is exact. The
same canary is forced through every failure path (dropped commit, unexpected
exception, lost terminal, ...) to prove that nothing - terminal text, logs,
exception text, files, database rows - carries it out of the reveal step.
"""
from __future__ import annotations

import ast
import builtins
import logging
import os
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.cli import provision as cli
from app.db.base import engine
from app.services import provisioning as prov
from app.services.certificates import hash_certificate_token
from tests.provisioning_helpers import (
    BACKEND_DIR,
    CANARY_TOKEN,
    EOF,
    PtySession,
    ScriptedTerminal,
    issue_answers,
    revoke_answers,
    rotate_answers,
    run_cli,
    world,  # noqa: F401  (fixture)
)
from tests.test_provisioning_cli import _failing_factory

CANARY_HASH = hash_certificate_token(CANARY_TOKEN)


@pytest.fixture()
def canary_token(monkeypatch):
    """Every token generated while this fixture is active is CANARY_TOKEN."""
    monkeypatch.setattr("app.services.certificates.generate_certificate_token", lambda: CANARY_TOKEN)


def _all_table_rows_as_text() -> str:
    with Session(bind=engine) as s:
        tables = s.execute(
            text(
                "select table_name from information_schema.tables "
                "where table_schema = 'public' and table_name <> 'alembic_version'"
            )
        ).scalars().all()
        chunks = []
        for table in tables:
            rows = s.execute(text(f'select t::text from "{table}" t')).scalars().all()  # noqa: S608 - fixed names
            chunks.append("\n".join(rows))
        return "\n".join(chunks)


def _assert_only_revealed(term: ScriptedTerminal, capsys=None, caplog=None) -> None:
    visible = term.visible_text
    assert CANARY_TOKEN not in visible
    assert CANARY_HASH not in visible and CANARY_HASH not in term.revealed_text
    if capsys is not None:
        captured = capsys.readouterr()
        assert CANARY_TOKEN not in captured.out + captured.err
    if caplog is not None:
        assert CANARY_TOKEN not in caplog.text and CANARY_HASH not in caplog.text


# --- the happy path ---------------------------------------------------------------------------------------------


def test_the_token_is_only_in_the_reveal_channel_and_only_its_hash_is_stored(world, canary_token, capsys, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    code, uid = world.piece(), world.uid()
    term = ScriptedTerminal(issue_answers(code, uid))

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_OK

    assert term.tokens() == [CANARY_TOKEN]
    assert term.urls() == [f"http://127.0.0.1:5500/c/{CANARY_TOKEN}"]  # the full URL, never the bare token
    assert CANARY_TOKEN not in term.visible_text
    assert all(CANARY_TOKEN not in line or "/c/" in line for line in term.revealed[0])
    _assert_only_revealed(term, capsys, caplog)

    rows = _all_table_rows_as_text()
    assert CANARY_TOKEN not in rows  # not in any column of any table
    [cert] = world.certificates(code)
    assert cert.token_hash == CANARY_HASH  # the hash is the only trace, and only here
    assert rows.count(CANARY_HASH) == 1
    assert CANARY_HASH not in world.tags(code)[0].notes


def test_rotate_shows_the_new_token_only_in_the_reveal_channel(world, monkeypatch, capsys, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    code = world.piece()
    _, uid, _, _ = world.issued(code, program=True)  # a real first token
    monkeypatch.setattr("app.services.certificates.generate_certificate_token", lambda: CANARY_TOKEN)
    term = ScriptedTerminal(rotate_answers(code, "compromised", tail=uid[-5:], choice="m"))

    assert run_cli(["rotate", "--piece", code], term) == cli.EXIT_OK

    assert term.tokens() == [CANARY_TOKEN]
    _assert_only_revealed(term, capsys, caplog)
    assert CANARY_TOKEN not in _all_table_rows_as_text()


def test_revoke_never_shows_any_secret_shaped_text(world, capsys, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    code = world.piece()
    token, *_ = world.issued(code, program=True)
    term = ScriptedTerminal(revoke_answers(code, "lost"))

    assert run_cli(["revoke", "--piece", code], term) == cli.EXIT_OK

    everything = term.visible_text + term.revealed_text + capsys.readouterr().out + caplog.text
    assert token not in everything and hash_certificate_token(token) not in everything


# --- every failure path -------------------------------------------------------------------------------------------------


class _RaisingAnswer:
    """A scripted answer that raises instead of answering."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def __call__(self, prompt):
        raise self.exc


def _leaky(exc_type=RuntimeError):
    return exc_type(f"secret in the message: {CANARY_TOKEN} {CANARY_HASH}")


def _scenario_commit_fails_before_the_url(world, code, uid, monkeypatch):
    return issue_answers(code, uid), {"session_factory": _failing_factory(1)}


def _scenario_commit_fails_after_the_url(world, code, uid, monkeypatch):
    return issue_answers(code, uid), {"session_factory": _failing_factory(2)}


def _scenario_unexpected_error_in_the_program_step(world, code, uid, monkeypatch):
    def boom(*args, **kwargs):
        raise _leaky()

    monkeypatch.setattr(prov, "execute_program", boom)
    return issue_answers(code, uid), {}


def _scenario_unexpected_error_in_the_issue_step(world, code, uid, monkeypatch):
    def boom(*args, **kwargs):
        raise _leaky(ValueError)

    monkeypatch.setattr(prov, "issue_certificate", boom)
    return issue_answers(code, uid), {}


def _scenario_self_check_fails(world, code, uid, monkeypatch):
    monkeypatch.setattr(prov, "verify_token_resolves", lambda *a, **k: False)
    return issue_answers(code, uid), {}


def _scenario_self_check_crashes(world, code, uid, monkeypatch):
    def boom(*args, **kwargs):
        raise _leaky()

    monkeypatch.setattr(prov, "verify_token_resolves", boom)
    return issue_answers(code, uid), {}


def _scenario_terminal_lost_after_the_url(world, code, uid, monkeypatch):
    return issue_answers(code, uid)[:5] + [EOF], {}


def _scenario_ctrl_c_after_the_url(world, code, uid, monkeypatch):
    return issue_answers(code, uid)[:5] + [_RaisingAnswer(KeyboardInterrupt())], {}


def _scenario_ctrl_c_while_the_url_is_shown(world, code, uid, monkeypatch):
    answers = issue_answers(code, uid)[:4]
    return answers + [_RaisingAnswer(KeyboardInterrupt())], {}


def _scenario_abort_and_revoke(world, code, uid, monkeypatch):
    return issue_answers(code, uid)[:5] + ["a", ""], {}


def _scenario_phone_scan_fails(world, code, uid, monkeypatch):
    return issue_answers(code, uid, scan="n"), {}


@pytest.mark.parametrize(
    "scenario",
    [
        _scenario_commit_fails_before_the_url,
        _scenario_commit_fails_after_the_url,
        _scenario_unexpected_error_in_the_program_step,
        _scenario_unexpected_error_in_the_issue_step,
        _scenario_self_check_fails,
        _scenario_self_check_crashes,
        _scenario_terminal_lost_after_the_url,
        _scenario_ctrl_c_after_the_url,
        _scenario_ctrl_c_while_the_url_is_shown,
        _scenario_abort_and_revoke,
        _scenario_phone_scan_fails,
    ],
)
def test_no_failure_path_lets_the_token_or_hash_out(world, canary_token, monkeypatch, capsys, caplog, scenario):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    code, uid = world.piece(), world.uid()
    answers, kwargs = scenario(world, code, uid, monkeypatch)
    term = ScriptedTerminal(answers)

    exit_code = run_cli(["issue", "--piece", code], term, **kwargs)  # must never raise

    assert exit_code != cli.EXIT_OK or scenario is _scenario_abort_and_revoke
    _assert_only_revealed(term, capsys, caplog)
    # A failing step names only the exception class, never its message.
    assert "secret in the message" not in term.visible_text
    # If the URL was shown at all, it was shown by `reveal` and by nothing else.
    assert set(term.tokens()) <= {CANARY_TOKEN}
    assert CANARY_TOKEN not in _all_table_rows_as_text()


def test_an_unexpected_error_reports_its_class_and_not_its_text(world, canary_token, monkeypatch):  # noqa: F811
    def boom(*args, **kwargs):
        raise _leaky(ValueError)

    monkeypatch.setattr(prov, "execute_issue", boom)
    code, uid = world.piece(), world.uid()
    term = ScriptedTerminal(issue_answers(code, uid))

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_UNEXPECTED

    assert "ValueError" in term.visible_text
    assert "secret in the message" not in term.visible_text


def test_a_database_error_carrying_the_token_reports_only_its_class(world, canary_token):  # noqa: F811
    class Sub(Session):
        def commit(self):
            raise OperationalError("INSERT ...", {"token": CANARY_TOKEN, "hash": CANARY_HASH}, Exception(CANARY_TOKEN))

    code, uid = world.piece(), world.uid()
    term = ScriptedTerminal(issue_answers(code, uid))

    assert run_cli(["issue", "--piece", code], term, session_factory=lambda: Sub(bind=engine, autoflush=False)) == cli.EXIT_DATABASE

    assert "OperationalError" in term.visible_text
    _assert_only_revealed(term)


# --- result objects and stored text ----------------------------------------------------------------------------------------


def test_result_objects_never_repr_the_token(world):  # noqa: F811
    code = world.piece()
    with Session(bind=engine) as s:
        issued = prov.execute_issue(s, code, world.uid(), operator="t")
        s.commit()
    with Session(bind=engine) as s:
        prov.execute_program(s, code, issued.tag_id, operator="t")
        rotated = prov.execute_rotate(s, code, reason="lost", new_physical_uid=None, operator="t")
        s.commit()
    for result, token in ((issued, issued.raw_token), (rotated, rotated.raw_token)):
        assert token not in repr(result) and token not in str(result)
        assert hash_certificate_token(token) not in repr(result)


# --- no side channels ------------------------------------------------------------------------------------------------------------


def test_the_flow_writes_no_files_and_no_temp_files(world, canary_token, monkeypatch, tmp_path):  # noqa: F811
    workdir, tempdir = tmp_path / "cwd", tmp_path / "tmp"
    workdir.mkdir()
    tempdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(tempfile, "tempdir", str(tempdir))

    writes = []
    real_open = builtins.open

    def recording_open(file, mode="r", *args, **kwargs):
        if any(flag in str(mode) for flag in "wax+"):
            writes.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", recording_open)
    code, uid = world.piece(), world.uid()

    assert run_cli(["issue", "--piece", code], ScriptedTerminal(issue_answers(code, uid))) == cli.EXIT_OK

    assert writes == []
    assert list(workdir.iterdir()) == [] and list(tempdir.iterdir()) == []


def test_core_dumps_are_disabled_before_anything_secret_exists(world, monkeypatch):  # noqa: F811
    import resource

    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda which, limits: calls.append((which, limits)))
    code, uid = world.piece(), world.uid()

    assert run_cli(["issue", "--piece", code], ScriptedTerminal(issue_answers(code, uid))) == cli.EXIT_OK

    assert calls == [(resource.RLIMIT_CORE, (0, 0))]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("relative", ["app/cli/provision.py", "app/services/provisioning.py"])
def test_the_provisioning_code_has_no_logging_files_or_traceback_machinery(relative):
    path = BACKEND_DIR / relative
    forbidden = {"logging", "traceback", "tempfile", "shutil", "pickle", "readline", "subprocess", "shelve", "sqlite3"}
    assert _imports(path) & forbidden == set()
    calls = {
        node.func.id
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {"print", "open", "exec", "eval"}


def test_the_reveal_uses_the_alternate_screen_and_always_restores_it(monkeypatch):
    class Recorder:
        def __init__(self):
            self.chunks = []

        def write(self, text_):
            self.chunks.append(text_)

        def flush(self):
            pass

    for failure in (None, cli.OperatorAbort):
        recorder = Recorder()
        monkeypatch.setattr(sys, "stdout", recorder)

        def ask(self, prompt, failure=failure):
            recorder.chunks.append("<ask>")
            if failure:
                raise failure

        monkeypatch.setattr(cli.ConsoleTerminal, "ask", ask)
        try:
            cli.ConsoleTerminal().reveal(["  https://example.invalid/c/SECRET"], "enter...")
        except cli.OperatorAbort:
            assert failure is cli.OperatorAbort
        text_ = "".join(recorder.chunks)
        assert text_.index(cli._ALT_SCREEN_ON) < text_.index("SECRET") < text_.index("<ask>") < text_.index(cli._ALT_SCREEN_OFF)
        assert text_.endswith(cli._ALT_SCREEN_OFF)  # the screen is restored even when the prompt fails
        assert "\x1b[3J" in cli._ALT_SCREEN_OFF  # best-effort scrollback clear


# --- a real terminal --------------------------------------------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "linux", reason="needs /proc and a real pseudo-terminal")
def test_a_real_terminal_session_keeps_the_token_out_of_argv_environ_and_scrollback(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    env = {**os.environ, "TERM": "xterm-256color"}
    session = PtySession([sys.executable, "-m", "app.cli.provision", "issue", "--piece", code], env)
    try:
        session.expect("reescriba su código: ")
        session.send(code)
        session.expect("léalo con su herramienta NFC): ")
        session.send(uid)
        session.expect("¿Es correcto? [s/N]: ")
        session.send("s")
        session.expect("para continuar: ")
        session.send(f"EMITIR {code}")
        session.expect("sin grabar la pantalla)...")
        session.send("")
        revealed = session.expect("Presione Enter para ocultarla...")

        assert revealed.startswith(cli._ALT_SCREEN_ON) or cli._ALT_SCREEN_ON in revealed
        import re

        [token] = re.findall(r"http://127\.0\.0\.1:5500/c/([A-Za-z0-9_-]{43})", revealed)
        # While the process holds the token in memory: not in argv, not in its environment.
        cmdline = Path(f"/proc/{session.pid}/cmdline").read_bytes().decode("utf-8", "replace")
        environ = Path(f"/proc/{session.pid}/environ").read_bytes().decode("utf-8", "replace")
        assert token not in cmdline and token not in environ
        assert "--token" not in cmdline

        session.send("")
        session.expect("[e] ya la escribí")
        session.send("e")
        session.expect("coincide con la mostrada? [s/N]: ")
        session.send("s")
        session.expect("(p. ej. E5:F6): ")
        session.send(uid[-5:])
        session.expect("¿Fue exactamente eso lo que se mostró? [s/N]: ")
        session.send("s")
        assert session.finish() == cli.EXIT_OK

        screen = session.buffer
        on, off = screen.index(cli._ALT_SCREEN_ON), screen.index(cli._ALT_SCREEN_OFF)
        assert on < screen.index(token) < off
        assert screen.count(token) == 1  # shown once, never echoed anywhere else
        assert token not in screen[off:]  # gone from the normal screen (and scrollback) afterwards
        [cert] = world.certificates(code)
        assert cert.token_hash == hash_certificate_token(token)
    finally:
        session.close()
