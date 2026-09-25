#!/usr/bin/env python3
"""Environment wrapper, port inspection, candidate smoke probes and a
read-only database probe for the N-08 deploy tool (ADR-027).

Secrets policy (docs/SECURITY.md, docs/DEPLOYMENT.md): shared/.env is loaded
here, in the parent, into the *environment* of child processes (Alembic,
Uvicorn, provisioning...). Secrets may live in an environment and in process
memory; they must never appear in argv, stdout, stderr, logs, the candidate
log, an artifact, RELEASE.json, the deploy log or a traceback. ``SecretGuard``
enforces that at the boundaries this module owns: it refuses argv that
contains a secret, scrubs captured output, and scans candidate logs and HTTP
bodies for canaries.

``python release_probe.py db-state`` is meant to be run *by a release's own
venv* (it imports psycopg lazily) with the env already loaded; it prints one
JSON document from a READ ONLY transaction and never writes.
"""
from __future__ import annotations

import atexit
import http.client
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_common as rc  # noqa: E402

ALLOWED_ENV_KEYS = ("APP_ENV", "DATABASE_URL", "DEBUG", "CORS_ALLOWED_ORIGINS")
_SECRET_NAME_HINTS = ("SECRET", "PASSWORD", "PASSWD", "TOKEN", "KEY", "DATABASE_URL", "CREDENTIAL")
_PASSTHROUGH_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR")
PLACEHOLDER_PASSWORDS = frozenset({"artesanfc", "change-me", "changeme", "password", "postgres", "ci"})
_TRUTHY = frozenset({"1", "true", "yes", "on", "t", "y"})

# Same flags as the production unit (docs/OPERATIONS.md); only the port differs.
UVICORN_FLAGS = ("--proxy-headers", "--forwarded-allow-ips", "127.0.0.1")


# --- secrets ----------------------------------------------------------------

class SecretGuard:
    """Holds secret *values* (never printed) and answers 'does this text
    contain one?'. Values shorter than 6 characters are ignored: they would
    match innocuous text and are not credentials worth guarding."""

    MIN_LENGTH = 6

    def __init__(self, values: Sequence[str] = ()) -> None:
        self._values: set[str] = set()
        self.add(*values)

    def add(self, *values: str) -> None:
        for value in values:
            if isinstance(value, str) and len(value) >= self.MIN_LENGTH:
                self._values.add(value)

    def merge(self, other: "SecretGuard") -> None:
        self._values |= other._values

    def __len__(self) -> int:
        return len(self._values)

    def contains(self, text: str) -> bool:
        return any(value in text for value in self._values)

    def scrub(self, text: str) -> str:
        for value in sorted(self._values, key=len, reverse=True):
            text = text.replace(value, "***")
        return text

    def check_argv(self, argv: Sequence[str]) -> None:
        for item in argv:
            if self.contains(str(item)):
                raise rc.OpsError(rc.Exit.INTERNAL, "refusing to run: a secret value would appear in argv")


# --- .env -------------------------------------------------------------------

@dataclass
class EnvFile:
    values: dict[str, str]  # only ALLOWED_ENV_KEYS
    ignored_keys: list[str]  # names only
    guard: SecretGuard = field(default_factory=SecretGuard)


def parse_env_text(text: str) -> dict[str, str]:
    """Plain ``KEY=VALUE`` lines. No interpolation, no command substitution.
    A matching pair of surrounding quotes is removed."""
    parsed: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        parsed[key] = value
    return parsed


def read_env_file(path: Path, *, enforce_mode: bool = True) -> EnvFile:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise rc.OpsError(rc.Exit.CONFIG, "shared/.env not found") from None
    if not stat.S_ISREG(st.st_mode):
        raise rc.OpsError(rc.Exit.CONFIG, "shared/.env must be a regular file, not a symlink")
    if enforce_mode:
        if st.st_mode & 0o077:
            raise rc.OpsError(rc.Exit.CONFIG, f"shared/.env mode is {stat.S_IMODE(st.st_mode):04o}; it must be 0600")
        if st.st_uid != os.geteuid():
            raise rc.OpsError(rc.Exit.CONFIG, "shared/.env must be owned by the service user")
    everything = parse_env_text(Path(path).read_text(encoding="utf-8", errors="replace"))
    values = {k: v for k, v in everything.items() if k in ALLOWED_ENV_KEYS}
    ignored = sorted(k for k in everything if k not in ALLOWED_ENV_KEYS)
    guard = SecretGuard()
    for key, value in everything.items():
        if any(hint in key.upper() for hint in _SECRET_NAME_HINTS):
            guard.add(value)
    url = values.get("DATABASE_URL", "")
    if url:
        try:
            parts = split_database_url(url)
        except rc.OpsError:
            parts = None
        if parts:
            guard.add(url, parts["password"], quote_like(parts["password"]))
            if len(parts["user"]) >= 8:
                guard.add(parts["user"])
    return EnvFile(values, ignored, guard)


def quote_like(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def split_database_url(url: str) -> dict[str, str]:
    """Decompose a postgres URL for libpq PG* variables. Raises a generic
    error (never echoing the URL) when it cannot be used."""
    try:
        parts = urlsplit(url.strip())
        query = parse_qs(parts.query)
        host = (query.get("host") or [parts.hostname or ""])[0]
        port = (query.get("port") or [str(parts.port or 5432)])[0]
    except ValueError:
        raise rc.OpsError(rc.Exit.CONFIG, "DATABASE_URL is not a valid URL") from None
    if parts.scheme not in ("postgresql", "postgres", "postgresql+psycopg"):
        raise rc.OpsError(rc.Exit.CONFIG, "DATABASE_URL must use a postgresql scheme")
    dbname = unquote(parts.path.lstrip("/"))
    if not host or not dbname or not parts.username:
        raise rc.OpsError(rc.Exit.CONFIG, "DATABASE_URL must name a host, a database and a user")
    return {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": unquote(parts.username),
        "password": unquote(parts.password or ""),
        "sslmode": (query.get("sslmode") or [""])[0],
    }


def pg_env(url: str) -> dict[str, str]:
    """PG* variables for pg_dump/pg_restore, so no credential is in argv."""
    p = split_database_url(url)
    env = {"PGHOST": p["host"], "PGPORT": p["port"], "PGDATABASE": p["dbname"], "PGUSER": p["user"]}
    if p["password"]:
        env["PGPASSWORD"] = p["password"]
    if p["sslmode"]:
        env["PGSSLMODE"] = p["sslmode"]
    return env


def validate_production_env(values: Mapping[str, str]) -> None:
    """Mirrors the app's own production guard (app/core/config.py) so a bad
    configuration is refused *before* a restart, without importing the app."""
    app_env = values.get("APP_ENV", "").strip().lower()
    if not app_env:
        raise rc.OpsError(rc.Exit.APP_ENV, "APP_ENV is not set in shared/.env")
    if app_env != "production":
        raise rc.OpsError(rc.Exit.APP_ENV, f"APP_ENV must be 'production' for a production root (got {app_env!r})")
    url = values.get("DATABASE_URL", "").strip()
    if not url:
        raise rc.OpsError(rc.Exit.CONFIG, "DATABASE_URL is not set in shared/.env")
    parts = split_database_url(url)
    if parts["password"].lower() in PLACEHOLDER_PASSWORDS or not parts["password"]:
        raise rc.OpsError(rc.Exit.CONFIG, "DATABASE_URL uses an empty or placeholder password")
    if values.get("DEBUG", "").strip().lower() in _TRUTHY:
        raise rc.OpsError(rc.Exit.CONFIG, "DEBUG must be false in production")
    origins = [o.strip() for o in values.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if rc.PRODUCTION_FRONTEND_ORIGIN not in origins:
        raise rc.OpsError(rc.Exit.CONFIG, f"CORS_ALLOWED_ORIGINS must include {rc.PRODUCTION_FRONTEND_ORIGIN}")
    if "*" in origins:
        raise rc.OpsError(rc.Exit.CONFIG, "CORS_ALLOWED_ORIGINS must not contain '*'")


def child_env(values: Mapping[str, str], extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """A *composed* environment, not a copy of the operator's: an exported
    DATABASE_URL in the shell cannot override shared/.env."""
    env = {k: os.environ[k] for k in _PASSTHROUGH_ENV if k in os.environ}
    env.update(values)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"})
    if extra:
        env.update(extra)
    return env


# --- process runner ----------------------------------------------------------

@dataclass
class RunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner:
    """Executes commands. argv is checked against the SecretGuard first;
    captured output is returned raw and must be scrubbed by the caller before
    display (``guard.scrub``)."""

    def __init__(self, guard: SecretGuard | None = None) -> None:
        # NOT `guard or SecretGuard()`: an empty guard is falsy (__len__), and
        # would be replaced by a *different* object the caller never fills.
        self.guard = guard if guard is not None else SecretGuard()

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | os.PathLike | None = None,
        timeout: float = 120,
        inherit_tty: bool = False,
    ) -> RunResult:
        self.guard.check_argv(argv)
        try:
            proc = subprocess.run(
                list(argv),
                env=dict(env) if env is not None else None,
                cwd=cwd,
                stdin=None if inherit_tty else subprocess.DEVNULL,
                stdout=None if inherit_tty else subprocess.PIPE,
                stderr=None if inherit_tty else subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return RunResult(127, "", f"command not found: {os.path.basename(str(argv[0]))}")
        except subprocess.TimeoutExpired:
            return RunResult(124, "", f"timed out after {timeout:.0f}s: {os.path.basename(str(argv[0]))}")
        return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")


# --- ports -------------------------------------------------------------------

def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _decode_addr(hex_addr: str) -> str:
    raw = bytes.fromhex(hex_addr)
    if len(raw) == 4:
        return socket.inet_ntoa(raw[::-1])
    swapped = b"".join(raw[i : i + 4][::-1] for i in range(0, 16, 4))
    return socket.inet_ntop(socket.AF_INET6, swapped)


@dataclass(frozen=True)
class Listener:
    address: str
    inode: int
    pid: int | None = None


def listeners_on(port: int, proc: str = "/proc") -> list[Listener]:
    """LISTEN sockets on ``port`` from /proc/net/tcp{,6}, with the owning pid
    when it can be resolved (same user, or root)."""
    found: list[Listener] = []
    for table in ("net/tcp", "net/tcp6"):
        try:
            lines = Path(proc, table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            addr_hex, port_hex = fields[1].split(":")
            if int(port_hex, 16) == port:
                found.append(Listener(_decode_addr(addr_hex), int(fields[9])))
    if not found:
        return []
    wanted = {item.inode for item in found}
    owners: dict[int, int] = {}
    try:
        pids = [int(n) for n in os.listdir(proc) if n.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        fd_dir = Path(proc, str(pid), "fd")
        try:
            for fd in os.listdir(fd_dir):
                try:
                    target = os.readlink(fd_dir / fd)
                except OSError:
                    continue
                if target.startswith("socket:[") and int(target[8:-1]) in wanted:
                    owners[int(target[8:-1])] = pid
        except OSError:
            continue
    return [Listener(item.address, item.inode, owners.get(item.inode)) for item in found]


@dataclass
class PortState:
    state: str  # free | expected | alien | unknown | exposed
    detail: str
    pid: int | None = None

    @property
    def ok(self) -> bool:
        return self.state in ("free", "expected")


def _cwd_of(pid: int, proc: str = "/proc") -> str | None:
    try:
        return os.path.realpath(os.readlink(Path(proc, str(pid), "cwd")))
    except OSError:
        return None


def port_owner_state(port: int, expected_cwd: str | os.PathLike | None, proc: str = "/proc") -> PortState:
    """Who holds ``127.0.0.1:port``? FAIL CLOSED: anything that is not the
    expected release (same working directory) is refused, and nothing is ever
    killed. Reports pid and cwd only (no argv, which could carry secrets)."""
    listeners = listeners_on(port, proc)
    if not listeners:
        return PortState("free", f"nothing listens on port {port}")
    exposed = [item.address for item in listeners if item.address not in ("127.0.0.1", "::1", "::ffff:127.0.0.1")]
    if exposed:
        return PortState("exposed", f"port {port} is listening on a non-loopback address ({exposed[0]})", listeners[0].pid)
    pids = {item.pid for item in listeners}
    if None in pids:
        return PortState("unknown", f"port {port} is held by a process this user cannot inspect")
    expected = os.path.realpath(expected_cwd) if expected_cwd else None
    for pid in sorted(p for p in pids if p is not None):
        cwd = _cwd_of(pid, proc)
        if cwd is None or expected is None or cwd != expected:
            shown = cwd if cwd else "unreadable"
            return PortState("alien", f"port {port} is held by pid {pid} (cwd {shown}), not by the expected release", pid)
    return PortState("expected", f"port {port} is held by the expected release", sorted(p for p in pids if p is not None)[0])


# --- HTTP + smoke ------------------------------------------------------------

@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes


def http_request(
    port: int,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 5.0,
    host: str = "127.0.0.1",
) -> HttpResult:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=dict(headers or {}))
        response = conn.getresponse()
        return HttpResult(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read(65536))
    finally:
        conn.close()


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _json(result: HttpResult) -> object:
    try:
        return json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


SYNTHETIC_TOKEN = "A" * 43  # well-formed, cannot exist: the resolve path only SELECTs


def run_smoke(port: int, release: Mapping, guard: SecretGuard, fetch: Callable[..., HttpResult] = http_request) -> list[Check]:
    """Black-box checks of a running candidate/service. Read-only by
    construction: GETs plus POST /certificates/resolve, which only SELECTs."""
    runtime = release["runtime"]
    checks: list[Check] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name, ok, detail))

    def guarded(result: HttpResult, name: str) -> HttpResult:
        if guard.contains(result.body.decode("utf-8", "replace")):
            record(f"{name}: no secret in response", False, "a secret value appeared in a response body")
        return result

    r = guarded(fetch(port, "GET", "/health"), "health")
    payload = _json(r)
    record("GET /health", r.status == 200 and payload == {"status": "ok", "database": "connected"}, f"status {r.status}")

    r = guarded(fetch(port, "GET", "/api/v1/artisans"), "artisans")
    payload = _json(r)
    ok = r.status == 200 and "application/json" in r.headers.get("content-type", "") and isinstance(payload, dict) and {"data", "meta"} <= set(payload)
    record("GET /api/v1/artisans returns JSON", ok, f"status {r.status}, content-type {r.headers.get('content-type', '-').split(';')[0]}")

    r = guarded(
        fetch(port, "POST", "/api/v1/certificates/resolve", headers={"Content-Type": "application/json"}, body=json.dumps({"token": SYNTHETIC_TOKEN}).encode()),
        "resolve",
    )
    payload = _json(r)
    record(
        "POST resolve (synthetic token) is 'unavailable'",
        r.status == 200 and payload == {"authenticity": {"status": "unavailable"}} and r.headers.get("cache-control") == "no-store",
        f"status {r.status}",
    )

    r = guarded(fetch(port, "POST", "/api/v1/certificates/resolve", headers={"Content-Type": "application/json"}, body=b"{}"), "resolve-422")
    payload = _json(r)
    code = payload.get("error", {}).get("code") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else None
    record("POST resolve {} is a 422 validation_error", r.status == 422 and code == "validation_error", f"status {r.status}")

    r = fetch(port, "GET", "/no-such-route")
    payload = _json(r)
    code = payload.get("error", {}).get("code") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else None
    record("unknown route is a 404 not_found envelope", r.status == 404 and code == "not_found", f"status {r.status}")

    docs = [fetch(port, "GET", path).status for path in ("/docs", "/redoc", "/openapi.json")]
    if not runtime["docs_in_production"]:
        record("docs/openapi are not served (release contract)", all(s == 404 for s in docs), f"statuses {docs}")
    else:
        record("docs/openapi status matches an older release (informational)", True, f"statuses {docs}")

    origin = rc.PRODUCTION_FRONTEND_ORIGIN
    r = fetch(port, "GET", "/api/v1/artisans", headers={"Origin": origin})
    record("CORS allows the production frontend origin", r.headers.get("access-control-allow-origin") == origin, "")
    r = fetch(port, "GET", "/api/v1/artisans", headers={"Origin": "https://evil.example"})
    record("CORS does not allow another origin", "access-control-allow-origin" not in r.headers, "")

    limit = runtime.get("resolve_body_limit_bytes")
    if limit:
        r = fetch(
            port, "POST", "/api/v1/certificates/resolve",
            headers={"Content-Type": "application/json"}, body=b'{"token":"' + b"A" * (limit + 64) + b'"}',
        )
        record(f"resolve body limit ({limit} B) answers 413", r.status == 413, f"status {r.status}")
    return checks


@dataclass
class CandidateReport:
    ok: bool
    checks: list[Check]
    log_path: str
    log_clean: bool
    stable: bool


def _terminate(proc: subprocess.Popen, grace: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=grace)


def run_candidate(
    *,
    release_dir: Path,
    env: Mapping[str, str],
    port: int,
    log_path: Path,
    release: Mapping,
    guard: SecretGuard,
    startup_timeout: float = 30.0,
    stability_seconds: float = 1.5,
    fetch: Callable[..., HttpResult] = http_request,
) -> CandidateReport:
    """Start the release on 127.0.0.1:``port`` with the production Uvicorn
    flags, run the smoke checks, and *always* kill it (its own process group).
    Never binds 0.0.0.0, never uses the production port, never writes to the
    database (see run_smoke). The 0600 log is scanned for secret canaries."""
    if port == rc.PRODUCTION_PORT:
        raise rc.OpsError(rc.Exit.CANDIDATE, "the candidate must not use the production port")
    if not port_is_free(port):
        raise rc.OpsError(rc.Exit.CANDIDATE, f"candidate port {port} is already in use")
    python = Path(release_dir) / "venv" / "bin" / "python"
    argv = [str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), *UVICORN_FLAGS]
    guard.check_argv(argv)

    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    checks: list[Check] = []
    stable = False
    proc: subprocess.Popen | None = None
    try:
        with os.fdopen(fd, "wb", buffering=0) as log:
            proc = subprocess.Popen(
                argv, cwd=str(release_dir), env=dict(env), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
            atexit.register(_terminate, proc)
            deadline = time.monotonic() + startup_timeout
            up = False
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    if fetch(port, "GET", "/health", timeout=2).status:
                        up = True
                        break
                except OSError:
                    time.sleep(0.2)
            if not up:
                checks.append(Check("candidate starts and answers /health", False, "process exited" if proc.poll() is not None else "startup timeout"))
            else:
                checks.append(Check("candidate starts and answers /health", True, ""))
                checks.extend(run_smoke(port, release, guard, fetch))
                time.sleep(stability_seconds)
                stable = proc.poll() is None
                checks.append(Check("process is stable after the checks", stable, "" if stable else "exited during the checks"))
    finally:
        if proc is not None:
            _terminate(proc)
    text = Path(log_path).read_text(errors="replace")
    clean = not guard.contains(text)
    checks.append(Check("candidate log contains no secret canary", clean, ""))
    return CandidateReport(all(c.ok for c in checks), checks, str(log_path), clean, stable)


# --- read-only database probe (runs inside a release venv) -------------------

def db_state() -> dict:
    import psycopg  # noqa: PLC0415 -- only the release venv has it
    from psycopg import sql  # noqa: PLC0415

    url = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql://", 1)
    if not url:
        raise rc.OpsError(rc.Exit.CONFIG, "DATABASE_URL is not set in the environment")
    with psycopg.connect(url, connect_timeout=5) as conn:
        cur = conn.cursor()
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SHOW server_version_num")
        server_version_num = int(cur.fetchone()[0])
        cur.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        versions: list[str] = []
        if cur.fetchone()[0]:
            cur.execute("SELECT version_num FROM public.alembic_version ORDER BY 1")
            versions = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        counts: dict[str, int] = {}
        for (table,) in cur.fetchall():
            cur.execute(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table)))
            counts[table] = int(cur.fetchone()[0])
        conn.rollback()
    return {"server_version_num": server_version_num, "alembic_versions": versions, "row_counts": counts}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["db-state"]:
        print("usage: release_probe.py db-state", file=sys.stderr)
        return int(rc.Exit.USAGE)
    try:
        print(json.dumps(db_state(), sort_keys=True))
    except rc.OpsError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return int(exc.code)
    except Exception as exc:  # noqa: BLE001 -- class name only: messages can echo the DSN
        print(f"error: database probe failed ({type(exc).__name__})", file=sys.stderr)
        return int(rc.Exit.ALEMBIC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
