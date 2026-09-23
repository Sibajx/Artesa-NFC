"""Shared, dependency-free building blocks of the N-08 release workflow.

Everything under ``backend/ops`` is standard library only on purpose: the
deploy tool runs with the server's system Python *before* any release venv
exists, and the builder runs in CI/on a developer machine without the app's
dependencies installed. ADR-027 (docs/DECISIONS.md) records the design.

Secret safety: nothing here formats a DATABASE_URL, password, user name or
host into an error message. ``OpsError`` messages are safe to print by
construction.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import PurePosixPath

PROJECT = "artesa-nfc"
TOOL_VERSION = "1.1.0"
BUILDER_VERSION = "3"  # 3: git.tag removed from RELEASE.json (reproducibility)
RELEASE_SCHEMA_VERSION = 1
# built_at_utc follows the SOURCE_DATE_EPOCH convention so the same commit
# always yields the same bytes: by default it is the commit time.
TIMESTAMP_SOURCES = ("commit", "source_date_epoch")
LEDGER_SCHEMA_VERSION = 1

DEFAULT_ROOT = "/home/energias/artesa-nfc"
PRODUCTION_REF = "origin/main"
PRODUCTION_REF_FULL = "refs/remotes/origin/main"
PRODUCTION_FRONTEND_ORIGIN = "https://artesanfc.com"
PUBLIC_API_BASE = "https://api.artesanfc.com"
PRODUCTION_PORT = 8000
CANDIDATE_PORT = 8001
# artesa-finanzas owns 127.0.0.1:8002 since the 2026-09-21 recovery. The tool
# never binds a candidate there (or on the production port).
FINANZAS_PORT = 8002
RESERVED_PORTS = frozenset({PRODUCTION_PORT, FINANZAS_PORT})
SERVICE_NAME = "artesa-nfc.service"

CHANNEL_PRODUCTION = "production"
CHANNEL_REHEARSAL = "rehearsal"

# The artifact is an allowlist, never "everything but". Anything else in the
# repository (tests, docs, .env*, caches, keys) simply cannot get in.
ARTIFACT_ROOT_DIRS = ("alembic", "app", "ops")
ARTIFACT_ROOT_FILES = ("alembic.ini", "requirements-prod.lock")
GENERATED_FILES = ("RELEASE.json", "MANIFEST.sha256")
REQUIRED_FILES = (
    "alembic.ini",
    "alembic/env.py",
    "app/main.py",
    "app/core/config.py",
    "app/core/db_safety.py",
    "ops/migration-classes.json",
    "requirements-prod.lock",
)
LEDGER_PATH = "ops/migration-classes.json"
LOCK_PATH = "requirements-prod.lock"
INSTALL_MODE = "pip-require-hashes-only-binary-no-deps"

# Bounds that keep a hostile or corrupt archive from exhausting the server.
MAX_MEMBERS = 5000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024

MIGRATION_CLASSES = ("baseline", "additive", "breaking")
CLASS_CODE_ONLY = "code-only"
CLASS_ADDITIVE = "migration/additive"
CLASS_BREAKING = "migration/breaking"

# Rollback policy (ADR-027, D13): what a deployment *is*, decided before it
# starts. Only CODE_ONLY may roll back automatically.
DEPLOY_CODE_ONLY = "CODE_ONLY"
DEPLOY_MIGRATION = "MIGRATION_DEPLOY"


def deploy_type(deployment_class: str) -> str:
    return DEPLOY_CODE_ONLY if deployment_class == CLASS_CODE_ONLY else DEPLOY_MIGRATION


class Exit(IntEnum):
    """Process exit codes (docs/DEPLOYMENT.md section 12).

    54 is the one addition to the approved family: activation failed and an
    automatic rollback was deliberately *not* attempted (a migration ran, or
    --no-auto-rollback was given). 51 keeps its meaning: a rollback was
    attempted or required and could not be completed.
    """

    OK = 0
    INTERNAL = 1
    USAGE = 2
    NO_TTY_OR_ABORT = 3
    ARTIFACT_INVALID = 10
    PREFLIGHT = 11
    PREPARE = 12
    CONFIG = 20
    APP_ENV = 21
    CANDIDATE = 22
    ALEMBIC = 23
    MIGRATION_NOT_AUTHORIZED = 30
    BACKUP = 31
    MIGRATION_FAILED = 32
    SMOKE = 40
    ACTIVATION_ROLLED_BACK = 50
    ACTIVATION_ROLLBACK_FAILED = 51
    ROLLBACK_INCOMPATIBLE = 52
    EDGE_FAILURE = 53
    ACTIVATION_NO_AUTO_ROLLBACK = 54


class OpsError(Exception):
    """A failure with a stable exit code and a message safe to print."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = message


# --- release id -------------------------------------------------------------

_RELEASE_ID_RE = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")


def validate_release_id(value: object) -> str:
    """Strictly ``YYYYMMDDTHHMMSSZ-<sha12>`` (UTC). Anything else is refused,
    including path separators, traversal, whitespace, upper-case hex, a real
    timestamp that does not exist (month 13) and a trailing newline."""
    if not isinstance(value, str) or not _RELEASE_ID_RE.fullmatch(value):
        raise OpsError(Exit.ARTIFACT_INVALID, "release id must match YYYYMMDDTHHMMSSZ-<12 hex>")
    try:
        datetime.strptime(value[:16], "%Y%m%dT%H%M%SZ")
    except ValueError:
        raise OpsError(Exit.ARTIFACT_INVALID, "release id carries an impossible timestamp") from None
    return value


def format_release_id(moment: datetime, commit: str) -> str:
    moment = moment.astimezone(timezone.utc)
    return validate_release_id(f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{commit[:12]}")


def artifact_filename(release_id: str) -> str:
    return f"artesa-nfc-{validate_release_id(release_id)}.tar.gz"


# --- hashing / manifest ------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


# One path segment: starts alphanumeric/underscore (so no dotfiles, no ".."),
# then a conservative charset. No backslash, no space, no NUL, no unicode.
_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.+-]*"
_PATH_RE = re.compile(rf"{_SEGMENT}(?:/{_SEGMENT})*")
_FORBIDDEN_NAME_RE = re.compile(
    r"(?:^|/)(?:\.env(?:\..*)?|.*\.(?:pem|key|p12|pfx|crt|sqlite3?|db|dump|log|pyc|pyo)"
    r"|id_(?:rsa|dsa|ecdsa|ed25519)(?:\..*)?|__pycache__|\.git(?:/.*)?"
    # virtualenvs, tool caches and editor/merge leftovers
    r"|\.?venv|site-packages|node_modules|\.(?:pytest|mypy|ruff)_cache"
    r"|.*~|.*\.(?:swp|swo|tmp|bak|orig|rej))(?:/|$)",
    re.IGNORECASE,
)
# Content that must never ship, whatever the file is called.
_SECRET_CONTENT_RE = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")


def validate_member_path(path: str) -> str:
    """A relative, normalized, allowlisted-charset POSIX path inside the
    artifact. Refuses traversal, absolute paths, backslashes and secrets-shaped
    names before anything is written anywhere."""
    if not isinstance(path, str) or not path or len(path) > 200:
        raise OpsError(Exit.ARTIFACT_INVALID, "invalid artifact member path")
    if _FORBIDDEN_NAME_RE.search(path):
        raise OpsError(Exit.ARTIFACT_INVALID, f"forbidden file name in artifact: {_short(path)}")
    if not _PATH_RE.fullmatch(path):
        raise OpsError(Exit.ARTIFACT_INVALID, f"unsafe artifact path: {_short(path)}")
    if PurePosixPath(path).as_posix() != path:
        raise OpsError(Exit.ARTIFACT_INVALID, f"non-normalized artifact path: {_short(path)}")
    return path


def assert_no_secret_content(path: str, data: bytes) -> None:
    """Refuse private-key material inside any packaged file."""
    if _SECRET_CONTENT_RE.search(data):
        raise OpsError(Exit.ARTIFACT_INVALID, f"secret material (private key) inside {_short(path)}")


def _short(text: str) -> str:
    """Bounded, printable rendering of an untrusted string for error text."""
    cleaned = "".join(ch if ch.isprintable() else "?" for ch in text[:60])
    return repr(cleaned)


def is_allowed_content_path(path: str) -> bool:
    if path in ARTIFACT_ROOT_FILES:
        return True
    return path.split("/", 1)[0] in ARTIFACT_ROOT_DIRS and "/" in path


def render_manifest(entries: Mapping[str, str]) -> bytes:
    """``sha256sum -c`` compatible, sorted by path, LF only. This is the
    *logical* content of a release: the same commit always renders the same
    bytes, which is what ``content_sha256`` hashes."""
    lines = [f"{entries[path]}  {path}\n" for path in sorted(entries)]
    return "".join(lines).encode("ascii")


_MANIFEST_LINE_RE = re.compile(r"([0-9a-f]{64})  (\S+)")


def parse_manifest(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise OpsError(Exit.ARTIFACT_INVALID, "MANIFEST.sha256 is not ASCII") from None
    entries: dict[str, str] = {}
    for line in text.splitlines():
        match = _MANIFEST_LINE_RE.fullmatch(line)
        if not match:
            raise OpsError(Exit.ARTIFACT_INVALID, "MANIFEST.sha256 has a malformed line")
        digest, path = match.groups()
        validate_member_path(path)
        if path in entries:
            raise OpsError(Exit.ARTIFACT_INVALID, "MANIFEST.sha256 lists a path twice")
        if not is_allowed_content_path(path) or path in GENERATED_FILES:
            raise OpsError(Exit.ARTIFACT_INVALID, f"MANIFEST.sha256 lists a path outside the allowlist: {_short(path)}")
        entries[path] = digest
    if render_manifest(entries) != data:
        raise OpsError(Exit.ARTIFACT_INVALID, "MANIFEST.sha256 is not in canonical form")
    return entries


# --- lockfile ---------------------------------------------------------------

_LOCK_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)", re.MULTILINE)
_LOCK_HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}")
_TEST_ONLY_PACKAGES = frozenset({"pytest", "pluggy", "iniconfig", "httpx", "httpcore", "pytest-asyncio", "coverage"})


def normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(text: str, *, target_python: str | None = None) -> dict[str, str]:
    """Pins of a ``pip-compile --generate-hashes`` lockfile. Every pin must
    carry at least one hash, and no test-only package may be present."""
    blocks = re.split(r"\n(?=[A-Za-z0-9][A-Za-z0-9._-]*==)", text)
    pins: dict[str, str] = {}
    for block in blocks:
        match = _LOCK_PIN_RE.match(block)
        if not match:
            continue
        name, version = normalize_package(match.group(1)), match.group(2)
        if not _LOCK_HASH_RE.search(block):
            raise OpsError(Exit.ARTIFACT_INVALID, f"lockfile pin without hashes: {name}")
        pins[name] = version
    if not pins:
        raise OpsError(Exit.ARTIFACT_INVALID, "lockfile has no pins")
    leaked = sorted(_TEST_ONLY_PACKAGES & pins.keys())
    if leaked:
        raise OpsError(Exit.ARTIFACT_INVALID, f"lockfile contains test-only packages: {', '.join(leaked)}")
    if target_python is not None:
        header = re.search(r"autogenerated by pip-compile with Python (\d+\.\d+)", text)
        if not header or header.group(1) != target_python:
            raise OpsError(Exit.ARTIFACT_INVALID, f"lockfile was not compiled with Python {target_python}")
    return pins


# --- alembic graph (parsed with ast: no alembic import needed) ----------------

def parse_alembic_revisions(files: Mapping[str, bytes]) -> list[dict]:
    """``files`` maps ``alembic/versions/<name>.py`` to its bytes. Returns the
    revisions in a deterministic topological order (parents first), each as
    ``{"revision": str, "down_revision": [str, ...]}``."""
    found: dict[str, list[str]] = {}
    for path, data in files.items():
        if not (path.startswith("alembic/versions/") and path.endswith(".py")):
            continue
        try:
            tree = ast.parse(data.decode("utf-8"), filename=path)
        except (SyntaxError, UnicodeDecodeError, ValueError):
            raise OpsError(Exit.ARTIFACT_INVALID, f"cannot parse migration file {_short(path)}") from None
        values: dict[str, object] = {}
        for node in tree.body:
            target = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value
            if isinstance(target, ast.Name) and target.id in ("revision", "down_revision"):
                try:
                    values[target.id] = ast.literal_eval(value)
                except ValueError:
                    raise OpsError(Exit.ARTIFACT_INVALID, f"non-literal revision in {_short(path)}") from None
        revision = values.get("revision")
        if not isinstance(revision, str) or "down_revision" not in values:
            raise OpsError(Exit.ARTIFACT_INVALID, f"migration without revision/down_revision: {_short(path)}")
        down = values["down_revision"]
        if down is None:
            parents: list[str] = []
        elif isinstance(down, str):
            parents = [down]
        elif isinstance(down, (tuple, list)) and all(isinstance(item, str) for item in down):
            parents = sorted(down)
        else:
            raise OpsError(Exit.ARTIFACT_INVALID, f"unsupported down_revision in {_short(path)}")
        if revision in found:
            raise OpsError(Exit.ARTIFACT_INVALID, "duplicate Alembic revision id")
        found[revision] = parents

    for revision, parents in found.items():
        for parent in parents:
            if parent not in found:
                raise OpsError(Exit.ARTIFACT_INVALID, f"revision {revision} has an unknown parent")
    ordered: list[dict] = []
    done: set[str] = set()
    remaining = dict(found)
    while remaining:
        ready = sorted(rev for rev, parents in remaining.items() if all(p in done for p in parents))
        if not ready:
            raise OpsError(Exit.ARTIFACT_INVALID, "Alembic revision graph has a cycle")
        for revision in ready:
            ordered.append({"revision": revision, "down_revision": remaining.pop(revision)})
            done.add(revision)
    return ordered


def alembic_heads(revisions: Iterable[Mapping]) -> list[str]:
    revisions = list(revisions)
    parents = {p for rev in revisions for p in rev["down_revision"]}
    return sorted(rev["revision"] for rev in revisions if rev["revision"] not in parents)


def ancestors(revisions: Iterable[Mapping], start: str) -> set[str]:
    """``start`` and everything it descends from."""
    graph = {rev["revision"]: list(rev["down_revision"]) for rev in revisions}
    if start not in graph:
        raise KeyError(start)
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph[current])
    return seen


def pending_revisions(revisions: list[Mapping], db_revision: str | None, head: str) -> list[str]:
    """Revisions ``upgrade head`` would apply, in application order. An empty
    database (``db_revision is None``) has the whole chain pending. Raises
    ``KeyError`` when the database is at a revision this release does not know
    (unknown, or ahead of the release)."""
    wanted = ancestors(revisions, head)
    applied = ancestors(revisions, db_revision) if db_revision is not None else set()
    if not applied <= wanted:
        raise KeyError(db_revision)
    order = [rev["revision"] for rev in revisions]
    return [rev for rev in order if rev in wanted and rev not in applied]


# --- migration ledger -------------------------------------------------------

def parse_ledger(data: bytes) -> dict[str, dict]:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise OpsError(Exit.ARTIFACT_INVALID, "migration ledger is not valid JSON") from None
    if not isinstance(obj, dict) or obj.get("schema_version") != LEDGER_SCHEMA_VERSION:
        raise OpsError(Exit.ARTIFACT_INVALID, "migration ledger has an unsupported schema_version")
    if set(obj) != {"schema_version", "revisions"} or not isinstance(obj["revisions"], dict):
        raise OpsError(Exit.ARTIFACT_INVALID, "migration ledger has unexpected fields")
    ledger: dict[str, dict] = {}
    for revision, entry in obj["revisions"].items():
        if (
            not isinstance(entry, dict)
            or entry.get("class") not in MIGRATION_CLASSES
            or not isinstance(entry.get("note"), str)
            or set(entry) != {"class", "note"}
        ):
            raise OpsError(Exit.ARTIFACT_INVALID, f"migration ledger entry {revision} is invalid")
        ledger[revision] = entry
    return ledger


def check_ledger_complete(revisions: Iterable[Mapping], ledger: Mapping[str, dict]) -> None:
    known = {rev["revision"] for rev in revisions}
    missing = sorted(known - ledger.keys())
    if missing:
        raise OpsError(
            Exit.ARTIFACT_INVALID,
            "Alembic revision(s) without a class in ops/migration-classes.json: " + ", ".join(missing),
        )
    stale = sorted(ledger.keys() - known)
    if stale:
        raise OpsError(Exit.ARTIFACT_INVALID, "migration ledger lists unknown revision(s): " + ", ".join(stale))


def classify_pending(pending: list[str], ledger: Mapping[str, dict]) -> str:
    if not pending:
        return CLASS_CODE_ONLY
    classes = {ledger[rev]["class"] for rev in pending}
    return CLASS_BREAKING if "breaking" in classes else CLASS_ADDITIVE


def rollback_compatibility(
    revisions: list[Mapping], ledger: Mapping[str, dict], db_revision: str | None, target_head: str
) -> tuple[bool, str]:
    """May the *database* as it is now serve a release whose head is
    ``target_head``? Yes only if every migration applied beyond that head is
    ``additive`` (old code tolerates additive schema). A ``breaking`` one, a
    revision the graph does not know, or a database *behind* the target head
    all say no. Never used to downgrade: the tool is forward-only."""
    if db_revision is None:
        return False, "database has no Alembic revision"
    try:
        applied = ancestors(revisions, db_revision)
        target = ancestors(revisions, target_head)
    except KeyError:
        return False, "database or target revision unknown to the release graph"
    if not target <= applied:
        return False, "database is behind the target release head"
    beyond = [rev["revision"] for rev in revisions if rev["revision"] in applied - target]
    bad = [rev for rev in beyond if ledger.get(rev, {}).get("class") != "additive"]
    if bad:
        return False, "migration(s) applied after the target head are not additive: " + ", ".join(bad)
    return True, "compatible" if not beyond else f"{len(beyond)} additive migration(s) beyond target head"


# --- RELEASE.json -----------------------------------------------------------

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_PY_MINOR = re.compile(r"3\.\d{1,2}")
_SECRETISH_VALUE = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://[^/\s:@]*:[^/\s@]*@)|(?:/home/)|(?:\b\d{1,3}(?:\.\d{1,3}){3}\b)|(?:postgres(?:ql)?://)",
    re.IGNORECASE,
)

_SCHEMA: dict[str, dict | None] = {
    "schema_version": None,
    "project": None,
    "release_id": None,
    "channel": None,
    "git": {"commit": None, "commit_short": None, "tree": None, "committed_at": None, "reachable_from": None},
    "build": {"built_at_utc": None, "timestamp_source": None, "builder_version": None, "target_python": None},
    "artifact": {"roots": None, "file_count": None, "content_sha256": None},
    "deps": {"lockfile": None, "lockfile_sha256": None, "install_mode": None},
    "alembic": {"head": None, "heads_count": None, "revisions": None},
    "runtime": {
        "app_env_required": None,
        "docs_in_production": None,
        "resolve_body_limit_bytes": None,
    },
}


def validate_release_json(obj: object, *, expected_id: str | None = None) -> dict:
    """Strict, closed schema: an unknown key anywhere is refused, which is what
    keeps hostnames, IPs, user names, DATABASE_URL and other server-specific
    or secret material out of the provenance record."""
    problems: list[str] = []

    def walk(value: object, schema: Mapping | None, where: str) -> None:
        if schema is None:
            return
        if not isinstance(value, dict):
            problems.append(f"{where}: expected object")
            return
        extra = sorted(set(value) - set(schema))
        missing = sorted(set(schema) - set(value))
        if extra:
            problems.append(f"{where}: unexpected field(s) {', '.join(extra)}")
        if missing:
            problems.append(f"{where}: missing field(s) {', '.join(missing)}")
        for key, sub in schema.items():
            if key in value:
                walk(value[key], sub, f"{where}.{key}" if where else key)

    walk(obj, _SCHEMA, "")
    if problems:
        raise OpsError(Exit.ARTIFACT_INVALID, "RELEASE.json invalid: " + "; ".join(problems[:4]))
    assert isinstance(obj, dict)

    def check(ok: bool, message: str) -> None:
        if not ok:
            raise OpsError(Exit.ARTIFACT_INVALID, f"RELEASE.json invalid: {message}")

    check(obj["schema_version"] == RELEASE_SCHEMA_VERSION, "unsupported schema_version")
    check(obj["project"] == PROJECT, "project")
    validate_release_id(obj["release_id"])
    if expected_id is not None:
        check(obj["release_id"] == expected_id, "release_id does not match the artifact name")
    check(obj["channel"] in (CHANNEL_PRODUCTION, CHANNEL_REHEARSAL), "unknown channel")
    git, build, art = obj["git"], obj["build"], obj["artifact"]
    deps, alem, runtime = obj["deps"], obj["alembic"], obj["runtime"]
    check(isinstance(git["commit"], str) and bool(_HEX40.fullmatch(git["commit"])), "git.commit")
    check(isinstance(git["tree"], str) and bool(_HEX40.fullmatch(git["tree"])), "git.tree")
    check(obj["release_id"].endswith(git["commit"][:12]), "release_id does not match git.commit")
    check(git["commit_short"] == git["commit"][:12], "git.commit_short")
    check(isinstance(git["committed_at"], str) and bool(_ISO_UTC.fullmatch(git["committed_at"])), "git.committed_at")
    if obj["channel"] == CHANNEL_PRODUCTION:
        check(git["reachable_from"] == PRODUCTION_REF, "production release must be reachable from origin/main")
    else:
        check(git["reachable_from"] is None, "rehearsal release must not claim reachability")
    check(isinstance(build["built_at_utc"], str) and bool(_ISO_UTC.fullmatch(build["built_at_utc"])), "build.built_at_utc")
    check(build["timestamp_source"] in TIMESTAMP_SOURCES, "build.timestamp_source")
    check(isinstance(build["builder_version"], str), "build.builder_version")
    check(isinstance(build["target_python"], str) and bool(_PY_MINOR.fullmatch(build["target_python"])), "build.target_python")
    check(art["roots"] == sorted([*ARTIFACT_ROOT_DIRS, *ARTIFACT_ROOT_FILES]), "artifact.roots")
    check(isinstance(art["file_count"], int) and not isinstance(art["file_count"], bool) and art["file_count"] > 0, "artifact.file_count")
    check(isinstance(art["content_sha256"], str) and bool(_HEX64.fullmatch(art["content_sha256"])), "artifact.content_sha256")
    check(deps["lockfile"] == LOCK_PATH, "deps.lockfile")
    check(isinstance(deps["lockfile_sha256"], str) and bool(_HEX64.fullmatch(deps["lockfile_sha256"])), "deps.lockfile_sha256")
    check(deps["install_mode"] == INSTALL_MODE, "deps.install_mode")
    check(isinstance(alem["head"], str) and alem["heads_count"] == 1, "alembic must have exactly one head")
    check(isinstance(alem["revisions"], list) and alem["revisions"], "alembic.revisions")
    for item in alem["revisions"]:
        check(
            isinstance(item, dict)
            and set(item) == {"revision", "down_revision"}
            and isinstance(item["revision"], str)
            and isinstance(item["down_revision"], list),
            "alembic.revisions entry",
        )
    check(alem["head"] in {item["revision"] for item in alem["revisions"]}, "alembic.head is not a revision")
    check(alembic_heads(alem["revisions"]) == [alem["head"]], "alembic.head does not match the revision graph")
    check(runtime["app_env_required"] == "production", "runtime.app_env_required")
    check(isinstance(runtime["docs_in_production"], bool), "runtime.docs_in_production")
    limit = runtime["resolve_body_limit_bytes"]
    check(limit is None or (isinstance(limit, int) and not isinstance(limit, bool) and limit > 0), "runtime.resolve_body_limit_bytes")

    def scan(value: object) -> None:
        if isinstance(value, str):
            check(not _SECRETISH_VALUE.search(value), "contains a host, IP, private path or credential-shaped value")
        elif isinstance(value, dict):
            for item in value.values():
                scan(item)
        elif isinstance(value, list):
            for item in value:
                scan(item)

    scan(obj)
    return obj


def canonical_json(obj: object) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
