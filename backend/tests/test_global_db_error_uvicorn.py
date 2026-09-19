"""Audit finding F-10, global scope - proof against a REAL Uvicorn process.

TestClient cannot show what the server prints: the leak was Uvicorn logging the
traceback that `ServerErrorMiddleware` re-raises. So these tests start
``uvicorn`` as a subprocess (real sockets, real logging config, real
PostgreSQL), drive real database failures carrying canaries, stop the server,
and inspect everything it wrote to stdout and stderr.

Reliability (this runs in CI):

* the listening socket is bound to port 0 by the test and handed to Uvicorn
  with ``--fd``: an ephemeral port, no reuse race, no fixed shared port;
* readiness is the "Uvicorn running on" log line (event-driven, no fixed
  sleeps), with a bounded startup timeout and an early-exit check;
* every request has a timeout; shutdown is SIGINT with a bounded wait and a
  SIGKILL fallback; the child is always reaped in ``finally``;
* both pipes are drained by threads, so a chatty child can never block.

Each scenario starts one server, sends all its requests, stops it, and only
then is the complete output inspected (the RuntimeError traceback is written
after the response, so reading earlier would race).
"""
from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import psycopg
import pytest
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.services.certificates import hash_certificate_token

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses --fd socket inheritance")

BACKEND_DIR = Path(__file__).resolve().parents[1]

STARTUP_TIMEOUT = 30.0
REQUEST_TIMEOUT = 10.0
SHUTDOWN_TIMEOUT = 15.0

_RUN_ID = uuid.uuid4().hex[:10]
CANARY_HASH = f"CanaryUvicornHash{_RUN_ID}"
CANARY_SQL = f"CANARY_SQL_LITERAL_{_RUN_ID}"
CANARY_RUNTIME = f"CANARY_RUNTIME_CONTROL_{_RUN_ID}"
PRIVATE_TOKEN = "CanaryUvicornPrivateTok" + "X" * 20  # a plausible 43-char token, never issued
assert len(PRIVATE_TOKEN) == 43
CANARY_DB_USER = f"canary_dbuser_{_RUN_ID}"
CANARY_DB_PASSWORD = f"CanaryDbPw_{_RUN_ID}"

GENERIC_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}

_DB_ERROR_LINE = re.compile(
    r"^event=db_error category=(?P<category>[a-z_]+) sqlstate=(?P<sqlstate>-|[0-9A-Z]{5}) "
    r"constraint=(?P<constraint>-|[A-Za-z0-9_]{1,63}) method=(?P<method>[A-Z]+) route=(?P<route>\S+)$"
)


@dataclass
class ServerRun:
    responses: dict[str, httpx.Response]
    stdout: str
    stderr: str
    returncode: int
    killed: bool

    @property
    def output(self) -> str:
        return self.stdout + "\n" + self.stderr

    def db_error_lines(self) -> list[re.Match]:
        matches = []
        for line in self.stderr.splitlines():
            if line.startswith("event=db_error"):
                match = _DB_ERROR_LINE.match(line)
                assert match, f"malformed db_error line: {line!r}"
                matches.append(match)
        return matches


def _drain(pipe, sink: list[str], ready: threading.Event | None) -> None:
    for line in iter(pipe.readline, ""):
        sink.append(line)
        if ready is not None and "Uvicorn running on" in line:
            ready.set()


def _run_server(requests: list[tuple[str, str, str, dict | None]], env_overrides: dict[str, str]) -> ServerRun:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    proc: subprocess.Popen | None = None
    threads: list[threading.Thread] = []
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    responses: dict[str, httpx.Response] = {}
    killed = False
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.set_inheritable(True)
        port = listener.getsockname()[1]

        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(BACKEND_DIR),
            "PROBE_CANARY_HASH": CANARY_HASH,
            "PROBE_CANARY_SQL": CANARY_SQL,
            "PROBE_CANARY_RUNTIME": CANARY_RUNTIME,
            **env_overrides,
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "tests.uvicorn_probe_app:app", "--fd", str(listener.fileno())],
            cwd=BACKEND_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=(listener.fileno(),),
        )
        ready = threading.Event()
        for pipe, sink, flag in ((proc.stdout, stdout_lines, None), (proc.stderr, stderr_lines, ready)):
            thread = threading.Thread(target=_drain, args=(pipe, sink, flag), daemon=True)
            thread.start()
            threads.append(thread)

        deadline = time.monotonic() + STARTUP_TIMEOUT
        while not ready.wait(0.1):
            if proc.poll() is not None:
                pytest.fail(f"uvicorn exited during startup ({proc.returncode}):\n{''.join(stderr_lines)[-2000:]}")
            if time.monotonic() > deadline:
                pytest.fail(f"uvicorn did not start within {STARTUP_TIMEOUT}s:\n{''.join(stderr_lines)[-2000:]}")

        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=REQUEST_TIMEOUT, trust_env=False) as client:
            for name, method, path, body in requests:
                responses[name] = client.request(method, path, json=body)
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=SHUTDOWN_TIMEOUT)
                except subprocess.TimeoutExpired:
                    killed = True
                    proc.kill()
                    proc.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()
        listener.close()

    return ServerRun(
        responses=responses,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
        returncode=proc.returncode,
        killed=killed,
    )


def _database_url_with(**changes) -> str:
    url = make_url(get_settings().database_url).set(drivername="postgresql", **changes)
    return url.render_as_string(hide_password=False)


# --- Scenario 1: real database up; three distinct failures + a non-DB control --------------------


@pytest.fixture(scope="module")
def failing_queries_run() -> ServerRun:
    return _run_server(
        [
            ("integrity", "GET", "/probe/integrity", None),
            ("pending", "GET", "/probe/pending-rollback", None),
            ("operational", "GET", "/probe/operational", None),
            ("runtime", "GET", "/probe/runtime", None),
        ],
        env_overrides={},
    )


def test_server_stops_cleanly(failing_queries_run):
    assert failing_queries_run.killed is False
    assert failing_queries_run.returncode == 0


def test_database_failures_return_the_generic_500(failing_queries_run):
    for name in ("integrity", "pending", "operational", "runtime"):
        response = failing_queries_run.responses[name]
        assert response.status_code == 500, name
        assert response.json() == GENERIC_500, name


def test_each_database_failure_writes_exactly_one_safe_event_line(failing_queries_run):
    lines = failing_queries_run.db_error_lines()
    by_route = {m["route"]: m for m in lines}

    assert sorted(by_route) == ["/probe/integrity", "/probe/operational", "/probe/pending-rollback"]
    assert len(lines) == 3  # one per failure, none for the RuntimeError control

    assert by_route["/probe/integrity"].groupdict() == {
        "category": "integrity",
        "sqlstate": "23505",
        "constraint": "certificate_token_hash_key",
        "method": "GET",
        "route": "/probe/integrity",
    }
    assert by_route["/probe/pending-rollback"]["category"] == "pending_rollback"
    assert by_route["/probe/operational"]["category"] == "operational"
    assert by_route["/probe/operational"]["sqlstate"] == "57014"


def test_no_database_text_reaches_stdout_or_stderr(failing_queries_run):
    output = failing_queries_run.output
    settings_password = make_url(get_settings().database_url).password
    forbidden = [
        CANARY_HASH,
        CANARY_SQL,
        "DETAIL",
        "Key (",
        "[SQL:",
        "SQL parameters",
        "Original exception",
        "psycopg.errors",
        "sqlalchemy.exc",
        "already exists",
        "postgresql://",
    ]
    if settings_password and len(settings_password) >= 6:
        forbidden.append(settings_password)
    for marker in forbidden:
        assert marker not in output, marker

    # No traceback was printed for any database failure: the only "Exception in
    # ASGI application" is the RuntimeError control's.
    assert output.count("Exception in ASGI application") == 1


def test_non_database_runtime_error_keeps_its_normal_server_traceback(failing_queries_run):
    stderr = failing_queries_run.stderr
    assert "Exception in ASGI application" in stderr
    assert "Traceback (most recent call last)" in stderr
    assert f"RuntimeError: non-database programmer error {CANARY_RUNTIME}" in stderr
    assert not any("/probe/runtime" == m["route"] for m in failing_queries_run.db_error_lines())


def test_premise_the_uvicorn_run_really_saw_the_failures(failing_queries_run):
    # The server did log the requests (access log), and the safe lines exist,
    # so the absence assertions above are about output that was really produced.
    for path in ("/probe/integrity", "/probe/pending-rollback", "/probe/operational", "/probe/runtime"):
        assert f'"GET {path} HTTP/1.1" 500' in failing_queries_run.stdout


# --- Scenario 2: database unreachable/unauthorized (connection failure) ---------------------------


@pytest.fixture(scope="module")
def connection_failure_run() -> ServerRun:
    bad_url = _database_url_with(username=CANARY_DB_USER, password=CANARY_DB_PASSWORD)
    return _run_server(
        [
            ("artisans", "GET", "/api/v1/artisans", None),
            ("resolve", "POST", "/api/v1/certificates/resolve", {"token": PRIVATE_TOKEN}),
        ],
        env_overrides={"DATABASE_URL": bad_url},
    )


def test_premise_the_raw_connection_error_contains_the_database_username():
    bad_url = _database_url_with(username=CANARY_DB_USER, password=CANARY_DB_PASSWORD)
    with pytest.raises(psycopg.OperationalError) as raw:
        psycopg.connect(bad_url, connect_timeout=5)
    assert CANARY_DB_USER in str(raw.value)


def test_connection_failure_returns_generic_500_and_logs_only_the_safe_line(connection_failure_run):
    run = connection_failure_run
    assert run.killed is False and run.returncode == 0

    for name in ("artisans", "resolve"):
        assert run.responses[name].status_code == 500, name
        assert run.responses[name].json() == GENERIC_500, name

    lines = run.db_error_lines()
    assert [(m["category"], m["method"], m["route"]) for m in lines] == [
        ("operational", "GET", "/api/v1/artisans"),
        ("operational", "POST", "/api/v1/certificates/resolve"),
    ]

    forbidden = [
        CANARY_DB_USER,
        CANARY_DB_PASSWORD,
        PRIVATE_TOKEN,
        hash_certificate_token(PRIVATE_TOKEN),
        "password authentication failed",
        "connection to server",
        "DETAIL",
        "[SQL:",
        "Traceback",
        "Exception in ASGI application",
        "psycopg",
        "sqlalchemy.exc",
    ]
    for marker in forbidden:
        assert marker not in run.output, marker


def test_resolve_database_failure_carries_no_store_and_never_logs_the_token(connection_failure_run):
    response = connection_failure_run.responses["resolve"]
    assert response.headers["cache-control"] == "no-store"
    assert PRIVATE_TOKEN not in response.text
    assert PRIVATE_TOKEN not in connection_failure_run.output
