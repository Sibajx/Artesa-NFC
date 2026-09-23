"""run_candidate against a REAL Uvicorn process serving a tiny ASGI app that
mimics the API contract the smoke checks expect (N-08, ADR-027)."""
import json
import os
import signal
import socket
import stat
import sys
import time
from pathlib import Path

import pytest

import release_common as rc
import release_probe as rp
from tests.ops.helpers import CANARY_PASSWORD, DATABASE_URL

APP = '''
import json, os, sys
LEAK = os.environ.get("TEST_LEAK")
BOOM = os.environ.get("TEST_BOOM")
HANG = os.environ.get("TEST_HANG")
BROKEN_HEALTH = os.environ.get("TEST_BROKEN_HEALTH")
if LEAK:
    print("starting with", LEAK, file=sys.stderr, flush=True)
if BOOM:
    raise RuntimeError("boom at import")
if HANG:
    import time; time.sleep(60)

async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    path, method = scope["path"], scope["method"]
    headers = {k.decode(): v.decode() for k, v in scope["headers"]}
    status, extra, body = 404, [], {"error": {"code": "not_found"}}
    if path == "/health":
        status, body = (500, {"status": "down"}) if BROKEN_HEALTH else (200, {"status": "ok", "database": "connected"})
    elif path == "/api/v1/artisans":
        status, body = 200, {"data": [], "meta": {"total": 0}}
        if headers.get("origin") == "https://artesanfc.com":
            extra.append((b"access-control-allow-origin", b"https://artesanfc.com"))
    elif path == "/api/v1/certificates/resolve" and method == "POST":
        msg = await receive(); raw = msg.get("body", b"")
        extra.append((b"cache-control", b"no-store"))
        if len(raw) > 1024: status, body = 413, {"error": {"code": "payload_too_large"}}
        elif raw == b"{}": status, body = 422, {"error": {"code": "validation_error"}}
        else: status, body = 200, {"authenticity": {"status": "unavailable"}}
    payload = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), *extra]})
    await send({"type": "http.response.body", "body": payload})
'''

RELEASE = {"runtime": {"app_env_required": "production", "docs_in_production": False, "resolve_body_limit_bytes": 1024}}


@pytest.fixture()
def release_dir(tmp_path):
    root = tmp_path / "rel"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "main.py").write_text(APP)
    (root / "venv" / "bin").mkdir(parents=True)
    wrapper = root / "venv" / "bin" / "python"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    wrapper.chmod(0o755)
    return root


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def candidate(release_dir, tmp_path, extra_env=None, **kw):
    port = free_port()
    env = rp.child_env({"APP_ENV": "production", "DATABASE_URL": DATABASE_URL, **(extra_env or {})})
    guard = rp.SecretGuard([CANARY_PASSWORD, DATABASE_URL])
    report = rp.run_candidate(release_dir=release_dir, env=env, port=port, log_path=tmp_path / f"cand-{port}.log", release=RELEASE, guard=guard, **kw)
    return port, report


def test_a_healthy_candidate_passes_every_check_and_is_killed(release_dir, tmp_path):
    port, report = candidate(release_dir, tmp_path)
    failed = [c for c in report.checks if not c.ok]
    assert report.ok and not failed, failed
    names = " | ".join(c.name for c in report.checks)
    for needle in ("/health", "artisans returns JSON", "synthetic token", "422", "404", "docs/openapi are not served", "CORS allows", "CORS does not allow", "413", "stable", "no secret canary"):
        assert needle in names, needle
    assert report.stable and report.log_clean
    assert rp.port_is_free(port)                      # the process is gone
    assert stat.S_IMODE(os.stat(report.log_path).st_mode) == 0o600


def test_candidate_binds_loopback_only_with_the_production_uvicorn_flags(release_dir, tmp_path, monkeypatch):
    seen = {}
    real = rp.subprocess.Popen
    def spy(argv, **kw):
        seen["argv"], seen["kw"] = list(argv), kw
        return real(argv, **kw)
    monkeypatch.setattr(rp.subprocess, "Popen", spy)
    candidate(release_dir, tmp_path)
    argv = seen["argv"]
    assert argv[argv.index("--host") + 1] == "127.0.0.1" and "0.0.0.0" not in argv
    assert argv[-3:] == ["--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"] and argv[1:4] == ["-m", "uvicorn", "app.main:app"]
    assert seen["kw"]["start_new_session"] is True and not any(CANARY_PASSWORD in a for a in argv)
    assert seen["kw"]["env"]["DATABASE_URL"] == DATABASE_URL     # in the environment, never in argv


def test_candidate_refuses_the_production_port_and_an_occupied_port(release_dir, tmp_path):
    guard = rp.SecretGuard()
    with pytest.raises(rc.OpsError, match="production port") as err:
        rp.run_candidate(release_dir=release_dir, env={}, port=rc.PRODUCTION_PORT, log_path=tmp_path / "x.log", release=RELEASE, guard=guard)
    assert err.value.code == rc.Exit.CANDIDATE
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); s.listen(1)
        with pytest.raises(rc.OpsError, match="already in use") as err:
            rp.run_candidate(release_dir=release_dir, env={}, port=s.getsockname()[1], log_path=tmp_path / "y.log", release=RELEASE, guard=guard)
        assert err.value.code == rc.Exit.CANDIDATE


def test_a_secret_in_the_candidate_log_fails_the_candidate(release_dir, tmp_path):
    port, report = candidate(release_dir, tmp_path, {"TEST_LEAK": CANARY_PASSWORD})
    assert not report.ok and not report.log_clean
    assert [c.name for c in report.checks if not c.ok] == ["candidate log contains no secret canary"]
    assert rp.port_is_free(port)


def test_a_candidate_that_crashes_on_import_fails_and_leaves_nothing_running(release_dir, tmp_path):
    port, report = candidate(release_dir, tmp_path, {"TEST_BOOM": "1"}, startup_timeout=10)
    assert not report.ok and report.checks[0].name.startswith("candidate starts") and not report.checks[0].ok
    assert rp.port_is_free(port)


def test_a_candidate_that_never_answers_times_out_and_is_killed(release_dir, tmp_path):
    started = time.monotonic()
    port, report = candidate(release_dir, tmp_path, {"TEST_HANG": "1"}, startup_timeout=1.5)
    assert not report.ok and "timeout" in report.checks[0].detail and time.monotonic() - started < 15
    assert rp.port_is_free(port)


def test_a_broken_health_endpoint_fails_the_smoke_checks(release_dir, tmp_path):
    port, report = candidate(release_dir, tmp_path, {"TEST_BROKEN_HEALTH": "1"})
    assert not report.ok and any(c.name == "GET /health" and not c.ok for c in report.checks)
    assert rp.port_is_free(port)


def test_smoke_flags_a_secret_echoed_in_a_response_body():
    guard = rp.SecretGuard([CANARY_PASSWORD])
    def leaky(port, method, path, **kw):
        return rp.HttpResult(500, {"content-type": "application/json"}, json.dumps({"error": DATABASE_URL}).encode())
    checks = rp.run_smoke(1, RELEASE, guard, leaky)
    assert any("no secret in response" in c.name and not c.ok for c in checks)


def test_smoke_expects_docs_open_only_for_releases_that_declare_it():
    def old_app(port, method, path, **kw):
        return rp.HttpResult(200, {"content-type": "application/json"}, b"{}")
    old = {"runtime": {"docs_in_production": True, "resolve_body_limit_bytes": None}}
    checks = rp.run_smoke(1, old, rp.SecretGuard(), old_app)
    assert any("informational" in c.name and c.ok for c in checks)
    assert not any("413" in c.name for c in checks)


def test_a_killed_group_takes_children_with_it(release_dir, tmp_path):
    (release_dir / "venv" / "bin" / "python").write_text(f'#!/bin/sh\n"{sys.executable}" -c "import time; time.sleep(60)" &\nexec "{sys.executable}" "$@"\n')
    port, report = candidate(release_dir, tmp_path)
    time.sleep(0.3)
    import subprocess
    leftovers = subprocess.run(["pgrep", "-f", "import time; time.sleep\\(60\\)"], capture_output=True, text=True).stdout.split()
    for pid in leftovers:  # clean up before asserting so a failure does not leak processes
        os.kill(int(pid), signal.SIGKILL)
    assert leftovers == []
