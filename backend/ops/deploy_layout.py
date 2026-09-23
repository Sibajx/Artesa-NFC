"""Directory layout, locking, atomic symlink switching, the deploy log,
retention and systemd inspection for the N-08 deploy tool (ADR-027).

    <root>/
      bin/        artesa-deploy launcher + installed copy of the tool (ops/)
      incoming/   artifacts + .sha256 sidecars copied from the build machine
      releases/<release-id>/   immutable, each with its own venv/
      current -> releases/<id>
      previous -> releases/<id>
      shared/{.env, backups/, state/, state/deployments/<ts>-<commit>/}

Only ``shared/`` holds state and secrets; a release directory is immutable
once prepared.
"""
from __future__ import annotations

import fcntl
import json
import os
import pwd
import re
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import release_common as rc
import release_probe as rp

PREPARED_MARKER = ".prepared"
STAGING_PREFIX = ".staging-"
_STALE_LOCK_NOTE = "deploy.lock"


@dataclass(frozen=True)
class Layout:
    root: Path

    @property
    def bin(self) -> Path: return self.root / "bin"
    @property
    def incoming(self) -> Path: return self.root / "incoming"
    @property
    def releases(self) -> Path: return self.root / "releases"
    @property
    def shared(self) -> Path: return self.root / "shared"
    @property
    def env_file(self) -> Path: return self.shared / ".env"
    @property
    def backups(self) -> Path: return self.shared / "backups"
    @property
    def state(self) -> Path: return self.shared / "state"
    @property
    def deploys(self) -> Path: return self.shared / "deploys"
    @property
    def current(self) -> Path: return self.root / "current"
    @property
    def previous(self) -> Path: return self.root / "previous"
    @property
    def lock_file(self) -> Path: return self.state / "deploy.lock"
    @property
    def deploy_log(self) -> Path: return self.state / "deploy-log.jsonl"
    @property
    def activation_marker(self) -> Path: return self.state / "activation.json"
    @property
    def rehearsal_log(self) -> Path: return self.state / "restore-rehearsals.jsonl"
    @property
    def evidence_root(self) -> Path: return self.state / "deployments"
    @property
    def restore_tmp(self) -> Path: return self.state / "restore-tmp"

    def release_dir(self, release_id: str) -> Path:
        return self.releases / rc.validate_release_id(release_id)

    def artifact_path(self, release_id: str) -> Path:
        return self.incoming / rc.artifact_filename(release_id)


def ensure_layout(layout: Layout) -> None:
    """Create the missing directories (modifying operations only). Never
    creates shared/.env: secrets are provisioned by a human."""
    layout.root.mkdir(parents=True, exist_ok=True)
    for path, mode in (
        (layout.bin, 0o755), (layout.incoming, 0o750), (layout.releases, 0o755),
        (layout.shared, 0o700), (layout.backups, 0o700), (layout.state, 0o700), (layout.deploys, 0o700),
        (layout.evidence_root, 0o700),
    ):
        path.mkdir(mode=mode, exist_ok=True)


# --- current / previous ---------------------------------------------------------

def read_link(layout: Layout, link: Path) -> str | None:
    """Release id a ``current``/``previous`` symlink points at, ``None`` when
    the link does not exist. Anything that is not a symlink to
    ``releases/<valid id>`` inside this root is refused: a real directory named
    ``current`` (the hybrid tree of the 2026-09-21 incident) is not a release."""
    try:
        st = os.lstat(link)
    except FileNotFoundError:
        return None
    if not stat.S_ISLNK(st.st_mode):
        raise rc.OpsError(rc.Exit.PREFLIGHT, f"{link.name} exists but is not a symlink to a release (hybrid/legacy layout?)")
    target = os.readlink(link)
    candidate = Path(target) if os.path.isabs(target) else layout.root / target
    normalized = Path(os.path.normpath(candidate))
    if normalized.parent != Path(os.path.normpath(layout.releases)):
        raise rc.OpsError(rc.Exit.PREFLIGHT, f"{link.name} does not point into releases/")
    release_id = rc.validate_release_id(normalized.name)
    if not (layout.releases / release_id).is_dir():
        raise rc.OpsError(rc.Exit.PREFLIGHT, f"{link.name} points at a release directory that does not exist")
    return release_id


def atomic_symlink(layout: Layout, link: Path, release_id: str) -> None:
    """Replace ``link`` with a symlink to ``releases/<id>`` in one atomic
    rename: a concurrent reader sees the old or the new target, never a
    missing link (no unlink-then-symlink gap)."""
    rc.validate_release_id(release_id)
    tmp = link.with_name(f"{link.name}.tmp-{os.getpid()}")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    os.symlink(f"releases/{release_id}", tmp)
    try:
        os.replace(tmp, link)
    except OSError:
        os.unlink(tmp)
        raise
    fd = os.open(layout.root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# --- prepared releases -------------------------------------------------------------

def read_prepared(release_dir: Path) -> dict | None:
    try:
        data = json.loads((release_dir / PREPARED_MARKER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    required = {"schema_version", "release_id", "prepared_at", "tool_version", "python", "lockfile_sha256", "release_json_sha256",
                "content_sha256", "archive_sha256"}
    if not isinstance(data, dict) or not required <= set(data) or data["release_id"] != release_dir.name:
        return None
    return data


def write_prepared(release_dir: Path, marker: dict) -> None:
    tmp = release_dir / f"{PREPARED_MARKER}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(marker, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, release_dir / PREPARED_MARKER)


@dataclass
class ReleaseInfo:
    name: str
    status: str  # prepared | unprepared | invalid
    detail: str = ""
    channel: str | None = None
    commit: str | None = None


def list_releases(layout: Layout) -> list[ReleaseInfo]:
    infos: list[ReleaseInfo] = []
    if not layout.releases.is_dir():
        return infos
    for entry in sorted(os.listdir(layout.releases)):
        path = layout.releases / entry
        if entry.startswith(STAGING_PREFIX):
            infos.append(ReleaseInfo(entry, "invalid", "leftover staging directory"))
            continue
        try:
            rc.validate_release_id(entry)
        except rc.OpsError:
            infos.append(ReleaseInfo(entry, "invalid", "name is not a release id"))
            continue
        if os.path.islink(path) or not path.is_dir():
            infos.append(ReleaseInfo(entry, "invalid", "not a real directory"))
            continue
        try:
            meta = json.loads((path / "RELEASE.json").read_text(encoding="utf-8"))
            rc.validate_release_json(meta, expected_id=entry)
        except (OSError, ValueError, rc.OpsError):
            infos.append(ReleaseInfo(entry, "invalid", "RELEASE.json missing or invalid"))
            continue
        marker = read_prepared(path)
        infos.append(
            ReleaseInfo(entry, "prepared" if marker else "unprepared", "", meta["channel"], meta["git"]["commit"][:12])
        )
    return infos


# --- lock --------------------------------------------------------------------------

def lock_is_held(layout: Layout) -> bool:
    """Read-only probe (creates nothing): is another operation running?"""
    try:
        fd = os.open(layout.lock_file, os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


@contextmanager
def deploy_lock(layout: Layout) -> Iterator[None]:
    """One modifying operation at a time (prepare/deploy/rollback/prune/backup).
    The lock is an flock on shared/state/deploy.lock: the kernel drops it if
    the tool dies, so there is no stale-lock cleanup to get wrong."""
    fd = os.open(layout.lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise rc.OpsError(rc.Exit.PREFLIGHT, "another deploy operation holds the deployment lock") from None
        os.ftruncate(fd, 0)
        os.write(fd, f"pid={os.getpid()}\n".encode())
        yield
    finally:
        os.close(fd)  # closing releases the flock


# --- activation marker -----------------------------------------------------------------

def write_activation_marker(layout: Layout, data: dict) -> None:
    tmp = layout.activation_marker.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, layout.activation_marker)


def read_activation_marker(layout: Layout) -> dict | None:
    try:
        return json.loads(layout.activation_marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_activation_marker(layout: Layout) -> None:
    try:
        os.unlink(layout.activation_marker)
    except FileNotFoundError:
        pass


# --- deploy log --------------------------------------------------------------------------

ALLOWED_LOG_KEYS = frozenset({
    "ts", "deploy_id", "event", "operator", "tool_version", "command", "source_release", "target_release",
    "git_sha", "deployment_class", "alembic_from", "alembic_to", "checks", "exit_code", "backup",
    "backup_sha256", "dry_run", "detail", "port", "rehearsal",
})


def operator_name() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return str(os.geteuid())


class DeployLog:
    """Append-only JSON Lines (shared/state/deploy-log.jsonl, 0600). Keys are
    an allowlist and every serialized line is scanned for secret canaries
    before it is written: a bug cannot turn the log into a leak. Never holds
    request bodies, environment or tokens."""

    def __init__(self, layout: Layout, guard: rp.SecretGuard, deploy_id: str, clock) -> None:
        self.layout, self.guard, self.deploy_id, self.clock = layout, guard, deploy_id, clock

    def event(self, event: str, **fields: object) -> None:
        record = {"ts": rc.utc_iso(self.clock()), "deploy_id": self.deploy_id, "event": event,
                  "operator": operator_name(), "tool_version": rc.TOOL_VERSION, **fields}
        unknown = set(record) - ALLOWED_LOG_KEYS
        if unknown:
            raise rc.OpsError(rc.Exit.INTERNAL, f"deploy log refuses unknown field(s): {sorted(unknown)}")
        line = json.dumps(record, sort_keys=True, ensure_ascii=True)
        if self.guard.contains(line):
            raise rc.OpsError(rc.Exit.INTERNAL, "deploy log refused an entry containing a secret value")
        fd = os.open(self.layout.deploy_log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)


# --- per-deployment evidence ----------------------------------------------------------------

class Evidence:
    """``shared/state/deployments/<UTC timestamp>-<commit12>[-<kind>]/``: one
    small JSON document per step (0600, directory 0700). Written as the
    deployment progresses, so an interrupted deployment still leaves the
    evidence of how far it got. Every document is scanned for secret values
    before it is written; nothing here ever holds environment values, a
    DATABASE_URL, request bodies or certificate tokens."""

    def __init__(self, directory: Path, guard: rp.SecretGuard) -> None:
        self.directory, self.guard = directory, guard

    @classmethod
    def create(cls, layout: Layout, guard: rp.SecretGuard, moment, commit: str, kind: str = "") -> "Evidence":
        layout.evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        base = f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{commit[:12]}" + (f"-{kind}" if kind else "")
        for attempt in range(1, 100):
            name = base if attempt == 1 else f"{base}.{attempt}"
            try:
                os.mkdir(layout.evidence_root / name, 0o700)
            except FileExistsError:
                continue
            return cls(layout.evidence_root / name, guard)
        raise rc.OpsError(rc.Exit.INTERNAL, "cannot allocate an evidence directory")

    def write(self, name: str, data: object) -> None:
        if not re.fullmatch(r"[a-z0-9-]+\.json", name):
            raise rc.OpsError(rc.Exit.INTERNAL, "invalid evidence file name")
        text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        if self.guard.contains(text):
            raise rc.OpsError(rc.Exit.INTERNAL, f"evidence {name} refused: it would contain a secret value")
        tmp = self.directory / f".{name}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.directory / name)


# --- retention -------------------------------------------------------------------------------

@dataclass
class PrunePlan:
    keep: list[str] = field(default_factory=list)
    delete: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)


def plan_prune(layout: Layout, keep_releases: int, protected: set[str]) -> PrunePlan:
    """current, previous and anything ``protected`` are never deleted; a
    directory without a valid RELEASE.json is never touched (refused, listed);
    among the rest the newest are kept up to ``keep_releases`` in total."""
    if keep_releases < 2:
        raise rc.OpsError(rc.Exit.USAGE, "--keep-releases must be at least 2 (current + previous)")
    plan = PrunePlan()
    fixed = {rid for rid in (read_link(layout, layout.current), read_link(layout, layout.previous)) if rid} | protected
    candidates: list[str] = []
    for info in list_releases(layout):
        if info.status == "invalid":
            plan.refused.append((info.name, info.detail or "not a recognized release"))
        elif info.name in fixed:
            plan.keep.append(info.name)
        else:
            candidates.append(info.name)
    room = max(0, keep_releases - len(plan.keep))
    candidates.sort(reverse=True)  # release ids start with a UTC timestamp
    plan.keep.extend(candidates[:room])
    plan.delete = candidates[room:]
    plan.keep.sort()
    return plan


def delete_release(layout: Layout, release_id: str) -> None:
    rc.validate_release_id(release_id)
    path = layout.releases / release_id
    if release_id in {read_link(layout, layout.current), read_link(layout, layout.previous)}:
        raise rc.OpsError(rc.Exit.PREFLIGHT, "refusing to delete the current or previous release")
    if os.path.islink(path) or not path.is_dir() or Path(os.path.realpath(path)).parent != Path(os.path.realpath(layout.releases)):
        raise rc.OpsError(rc.Exit.PREFLIGHT, "refusing to delete something that is not a plain release directory")
    shutil.rmtree(path)


# --- systemd (read-only inspection) -------------------------------------------------------------

@dataclass
class ServiceInfo:
    available: bool = False
    active_state: str = ""
    sub_state: str = ""
    n_restarts: int = 0
    working_directory: str = ""
    exec_path: str = ""

    @property
    def crash_looping(self) -> bool:
        return self.available and (self.sub_state == "auto-restart" or (self.active_state != "active" and self.n_restarts >= 20))


_SHOW_PROPS = ("ActiveState", "SubState", "NRestarts", "WorkingDirectory", "ExecStart")


def read_service_info(runner: rp.Runner, service: str = rc.SERVICE_NAME) -> ServiceInfo:
    argv = ["systemctl", "show", service, *[f"--property={p}" for p in _SHOW_PROPS]]
    result = runner.run(argv, env={k: v for k, v in os.environ.items() if k in ("PATH", "LANG")}, timeout=15)
    if result.returncode != 0:
        return ServiceInfo(False)
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    match = re.search(r"path=(\S+)", props.get("ExecStart", ""))
    try:
        restarts = int(props.get("NRestarts", "0") or 0)
    except ValueError:
        restarts = 0
    return ServiceInfo(True, props.get("ActiveState", ""), props.get("SubState", ""), restarts, props.get("WorkingDirectory", ""), match.group(1) if match else "")


def unit_points_at_current(layout: Layout, info: ServiceInfo) -> tuple[bool, str]:
    """The unit must run from ``<root>/current`` (WorkingDirectory and the venv
    interpreter). A unit that runs from a shared backend directory is exactly
    how finanzas and ArtesaNFC collided on 2026-09-21."""
    if not info.available:
        return False, "systemd unit could not be inspected"
    expected_wd = str(layout.current)
    expected_exec = f"{layout.current}/venv/bin/"
    if info.working_directory != expected_wd:
        return False, "unit WorkingDirectory is not <root>/current"
    if not info.exec_path.startswith(expected_exec):
        return False, "unit ExecStart does not use <root>/current/venv"
    return True, "unit runs from <root>/current"
