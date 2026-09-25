"""The production edge behaviours against a REAL Uvicorn process over real
sockets (audit findings N-03 / F-14, docs/OPERATIONS.md).

Two groups:

* the APP_ENV=production profile, exactly as the systemd unit runs it (no
  docs, /health that fails with the database, the resolve body limit with CORS
  and no-store on the 413, no payload in the logs);
* forwarded-header trust: what Uvicorn does with X-Forwarded-For /
  X-Forwarded-Proto behind the Cloudflare Tunnel, which docs/OPERATIONS.md
  requires to be `--proxy-headers --forwarded-allow-ips 127.0.0.1` (never `*`).
  The application does not read those headers itself; this pins what the
  documented flags actually deliver.

Reliability (same approach as tests/test_global_db_error_uvicorn.py): the
listening socket is bound to port 0 by the test and handed over with ``--fd``
(ephemeral port, no reuse race); readiness is the "Uvicorn running on" log line
with a bounded timeout and an early-exit check; every socket operation has a
timeout; shutdown is SIGINT with a SIGKILL fallback and the child is always
reaped. Bodies are sent as raw bytes in ONE write so the result never depends
on how a client library reacts to an early response.
"""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses --fd socket inheritance")

BACKEND_DIR = Path(__file__).resolve().parents[1]
STARTUP_TIMEOUT = 30.0
SOCKET_TIMEOUT = 10.0
SHUTDOWN_TIMEOUT = 15.0
LOG_WAIT = 5.0

RESOLVE = "/api/v1/certificates/resolve"
PRODUCTION_ORIGIN = "https://artesanfc.com"
# Production-grade dummy (passes the "no placeholder password" guard) that is
# never reachable: port 9 (discard) refuses, so /health sees a database outage.
DUMMY_DATABASE_URL = "postgresql://edge_user:edge-not-a-secret@127.0.0.1:9/edge_test"
CANARY = "EdgeCanaryPayloadMustNeverBeLogged"

TOO_LARGE = {"error": {"code": "payload_too_large", "message": "The request body is too large."}}
NOT_FOUND = {"error": {"code": "not_found", "message": "The requested resource does not exist."}}


class Server:
    def __init__(self, env_overrides: dict[str, str], extra_args: tuple[str, ...] = ()):
        self._env_overrides = env_overrides
        self._extra_args = extra_args
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._proc: subprocess.Popen | None = None
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._threads: list[threading.Thread] = []
        self.port = 0

    def output(self) -> str:
        return "".join(self._stdout) + "\n" + "".join(self._stderr)

    def wait_for_log(self, pattern: str) -> re.Match:
        """Block (bounded) until a log line matches (Uvicorn writes its access
        log to stdout and everything else to stderr), so assertions on the log
        never race the server writing it."""
        deadline = time.monotonic() + LOG_WAIT
        regex = re.compile(pattern)
        while True:
            for line in [*self._stdout, *self._stderr]:
                match = regex.search(line)
                if match:
                    return match
            if time.monotonic() > deadline:
                pytest.fail(f"no log line matching {pattern!r} within {LOG_WAIT}s:\n{self.output()[-2000:]}")
            time.sleep(0.05)

    def __enter__(self) -> "Server":
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(128)
        self._listener.set_inheritable(True)
        self.port = self._listener.getsockname()[1]
        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(BACKEND_DIR),
            "PYTHONDONTWRITEBYTECODE": "1",
            **self._env_overrides,
        }
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--fd", str(self._listener.fileno()), *self._extra_args],
            cwd=BACKEND_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=(self._listener.fileno(),),
        )
        ready = threading.Event()

        def drain(pipe, sink, flag):
            for line in iter(pipe.readline, ""):
                sink.append(line)
                if flag is not None and "Uvicorn running on" in line:
                    flag.set()

        for pipe, sink, flag in ((self._proc.stdout, self._stdout, None), (self._proc.stderr, self._stderr, ready)):
            thread = threading.Thread(target=drain, args=(pipe, sink, flag), daemon=True)
            thread.start()
            self._threads.append(thread)

        deadline = time.monotonic() + STARTUP_TIMEOUT
        while not ready.wait(0.1):
            if self._proc.poll() is not None:
                self.__exit__(None, None, None)
                pytest.fail(f"uvicorn exited during startup:\n{self.output()[-2000:]}")
            if time.monotonic() > deadline:
                self.__exit__(None, None, None)
                pytest.fail(f"uvicorn did not start within {STARTUP_TIMEOUT}s:\n{self.output()[-2000:]}")
        return self

    def __exit__(self, *exc_info) -> None:
        proc = self._proc
        if proc is not None:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=SHUTDOWN_TIMEOUT)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            for thread in self._threads:
                thread.join(timeout=5)
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()
        self._listener.close()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=SOCKET_TIMEOUT, trust_env=False)

    def raw(self, request: bytes) -> tuple[int, dict[str, str], bytes]:
        """Send `request` in one write on a fresh connection and read one
        complete response (status, lower-cased headers, body)."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=SOCKET_TIMEOUT) as conn:
            conn.sendall(request)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                assert chunk, "connection closed before the response headers"
                data += chunk
            head, _, body = data.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            status = int(lines[0].split()[1])
            headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
            want = int(headers.get("content-length", "0"))
            while len(body) < want:
                chunk = conn.recv(65536)
                assert chunk, "connection closed before the response body"
                body += chunk
            return status, headers, body


# --- Production profile -----------------------------------------------------------------

PRODUCTION_ENV = {
    "APP_ENV": "production",
    "DEBUG": "false",
    "CORS_ALLOWED_ORIGINS": PRODUCTION_ORIGIN,
    "DATABASE_URL": DUMMY_DATABASE_URL,
}


@pytest.fixture(scope="module")
def production_server():
    with Server(PRODUCTION_ENV) as server:
        yield server


def _post(server: Server, body: bytes, *, extra_headers: str = "", chunked: bool = False, path: str = RESOLVE) -> tuple:
    head = (
        f"POST {path} HTTP/1.1\r\nHost: api.artesanfc.com\r\nOrigin: {PRODUCTION_ORIGIN}\r\n"
        f"Content-Type: application/json\r\nConnection: close\r\n{extra_headers}"
    )
    if chunked:
        pieces = [body[i : i + 400] for i in range(0, len(body), 400)]
        encoded = b"".join(f"{len(p):x}\r\n".encode() + p + b"\r\n" for p in pieces) + b"0\r\n\r\n"
        return server.raw((head + "Transfer-Encoding: chunked\r\n\r\n").encode() + encoded)
    return server.raw((head + f"Content-Length: {len(body)}\r\n\r\n").encode() + body)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_production_serves_no_docs_over_real_http(production_server, path):
    with production_server.client() as client:
        response = client.get(path)
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_production_health_is_503_when_the_database_is_unreachable(production_server):
    with production_server.client() as client:
        get = client.get("/health")
        head = client.head("/health")
    assert get.status_code == 503
    assert get.json() == {"status": "unavailable", "database": "unavailable"}
    assert get.headers["cache-control"] == "no-store"
    assert head.status_code == 503
    assert head.headers["cache-control"] == "no-store"
    assert head.content == b""


def test_content_length_over_the_limit_is_413_with_cors_and_no_store(production_server):
    status, headers, body = _post(production_server, b"x" * 2000)
    assert status == 413
    assert json.loads(body) == TOO_LARGE
    assert headers["cache-control"] == "no-store"
    assert headers["access-control-allow-origin"] == PRODUCTION_ORIGIN


def test_a_chunked_body_over_the_limit_is_413_with_cors_and_no_store(production_server):
    status, headers, body = _post(production_server, b"x" * 2000, chunked=True)
    assert status == 413
    assert json.loads(body) == TOO_LARGE
    assert headers["cache-control"] == "no-store"
    assert headers["access-control-allow-origin"] == PRODUCTION_ORIGIN


def test_a_body_within_the_limit_works_and_the_server_keeps_serving(production_server):
    ok = json.dumps({"token": "abc"}).encode()
    exactly = ok + b" " * (1024 - len(ok))
    assert len(exactly) == 1024
    for body in (ok, exactly):
        status, headers, payload = _post(production_server, body)
        assert status == 200
        assert json.loads(payload) == {"authenticity": {"status": "unavailable"}}
        assert headers["cache-control"] == "no-store"
    one_over = ok + b" " * (1025 - len(ok))
    assert _post(production_server, one_over)[0] == 413
    # Still alive after rejections that left request bodies unread.
    with production_server.client() as client:
        assert client.get("/api/v1/nope").status_code == 404


def test_the_trailing_slash_form_is_limited_over_real_http(production_server):
    status, _, body = _post(production_server, b"x" * 2000, path=RESOLVE + "/")
    assert status == 413 and json.loads(body) == TOO_LARGE


def test_the_rejected_payload_reaches_no_log(production_server):
    payload = json.dumps({"token": CANARY * 40}).encode()
    assert len(payload) > 1024
    status, _, body = _post(production_server, payload)
    assert status == 413
    assert CANARY.encode() not in body
    # The access line for that very request is written after the response;
    # wait for it, then inspect everything the server has printed.
    production_server.wait_for_log(rf'"POST {re.escape(RESOLVE)} HTTP/1\.1" 413')
    assert CANARY not in production_server.output()


# --- Forwarded-header trust --------------------------------------------------------------

SPOOFED_AND_REAL = "203.0.113.7, 198.51.100.4"  # client-supplied entry, then what Cloudflare appended
TEST_ENV = {"APP_ENV": "test"}  # the suite's own test DATABASE_URL is inherited


def _client_address_logged(server: Server, path: str) -> str:
    return server.wait_for_log(rf'(\S+?):\d+ - "GET {re.escape(path)} HTTP/1\.1"').group(1)


def test_trusted_proxy_flags_take_the_rightmost_untrusted_address_and_the_scheme():
    with Server(TEST_ENV, ("--proxy-headers", "--forwarded-allow-ips", "127.0.0.1")) as server:
        with server.client() as client:
            client.get("/edge-probe-trusted", headers={"X-Forwarded-For": SPOOFED_AND_REAL})
            redirect = client.post(
                RESOLVE + "/", json={"token": "a"}, headers={"X-Forwarded-Proto": "https"}, follow_redirects=False
            )
        # The address Cloudflare appended wins over the one the client wrote.
        assert _client_address_logged(server, "/edge-probe-trusted") == "198.51.100.4"
        assert "203.0.113.7" not in server.output()
        assert redirect.status_code == 307
        assert redirect.headers["location"].startswith("https://")


def test_a_peer_that_is_not_trusted_cannot_set_the_client_address_or_scheme():
    with Server(TEST_ENV, ("--proxy-headers", "--forwarded-allow-ips", "192.0.2.1")) as server:
        with server.client() as client:
            client.get("/edge-probe-untrusted", headers={"X-Forwarded-For": SPOOFED_AND_REAL})
            redirect = client.post(
                RESOLVE + "/", json={"token": "a"}, headers={"X-Forwarded-Proto": "https"}, follow_redirects=False
            )
        assert _client_address_logged(server, "/edge-probe-untrusted") == "127.0.0.1"
        assert "198.51.100.4" not in server.output() and "203.0.113.7" not in server.output()
        assert redirect.headers["location"].startswith("http://")
