#!/usr/bin/env python3
"""artesa-backup: scheduled, encrypted backups of the production database
(#126 / D10, phase D10.1 -- local encrypted foundation).

    artesa-backup run [--scheduled]
    artesa-backup status [--json]
    artesa-backup verify [<backup-id> | --all]
    artesa-backup restore-test <dump-or-extracted-bundle-dir>
    artesa-backup remote-check [--ping-deadman]

Standard library only, plus the ``age`` binary for encryption and the
PostgreSQL 18 programs the deploy tool already uses. Separate from
``artesa-deploy``: its own lock, state and life cycle (D10 decision 5).

Contract (D10 decisions, docs/BACKUP.md):
  * the server holds only PUBLIC age recipients (K1 + K2); private keys never
    live on the host, so nothing here can decrypt;
  * ZERO persistent plaintext: the dump exists only in a private staging
    directory (0700, files 0600) while it is verified, restore-checked and
    encrypted; it is unlinked as soon as the verified ciphertext is in place.
    Unlink is not a secure erase on SSDs/journaling filesystems: the guarantee
    is no deliberate persistence and minimal exposure;
  * no fallback to an unencrypted backup: missing recipients or a missing
    ``age`` fail the run and leave no backup and no plaintext;
  * off-host copies (D10.2, backup_remote.py) are optional configuration:
    without ``shared/backup/remote.env`` ``status`` says ``OFFSITE: NOT
    CONFIGURED``. With it, every run uploads the ciphertext + meta.json with
    a no-delete credential and verifies them; a local backup without a
    verified remote copy is never removed by retention. ``D10: INCOMPLETE``
    until the recovery drills of D10.3.

Nothing here calls sudo, writes to the production database or reads a
private key. Exit codes: docs/DEPLOYMENT.md §12 (0, 1, 2, 10, 11, 20, 31).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import socket
import stat
import sys
import tarfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup_remote as br  # noqa: E402
import deploy_db as ddb  # noqa: E402
import deploy_layout as dl  # noqa: E402
import release_common as rc  # noqa: E402
import release_probe as rp  # noqa: E402

MANIFEST_SCHEMA = 1
STATE_SCHEMA = 1
KEEP_LOCAL = 7
STALE_AFTER_HOURS = 26
DEPLOY_LOCK_WAIT = 600.0     # seconds to wait for the deploy lock around pg_dump
DEPLOY_LOCK_POLL = 10.0
AGE_TIMEOUT = 600.0
AGE_MAGIC = b"age-encryption.org/v1\n"
BUNDLE_NAME = "bundle.tar.age"
META_NAME = "meta.json"
RECIPIENT_KEYS = ("ARTESA_BACKUP_AGE_RECIPIENT_K1", "ARTESA_BACKUP_AGE_RECIPIENT_K2")
_RECIPIENT_RE = re.compile(r"age1[02-9ac-hj-np-z]{58}")
_BACKUP_ID_RE = re.compile(r"\d{8}T\d{6}Z-(?:[0-9a-f]{12}|nocommit)")
_X25519_STANZA = re.compile(rb"^-> X25519 [A-Za-z0-9+/]{43}$")
ALLOWED_LOG_KEYS = frozenset({"ts", "run_id", "event", "backup_id", "result", "exit_code", "detail", "tool_version",
                              "encrypted_sha256", "encrypted_size", "release_id", "git_sha", "alembic", "removed",
                              "provider", "objects", "uploaded"})


# --- layout -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class BackupLayout:
    """``<root>/shared/backup/`` (0700). Deliberately separate from
    ``shared/backups/`` (the deploy tool's pre-migration dumps), which this
    tool never reads, moves or deletes."""
    root: Path

    @property
    def base(self) -> Path: return self.root / "shared" / "backup"
    @property
    def config(self) -> Path: return self.base / "backup.env"
    @property
    def staging(self) -> Path: return self.base / "staging"
    @property
    def encrypted(self) -> Path: return self.base / "encrypted"
    @property
    def state_dir(self) -> Path: return self.base / "state"
    @property
    def status_file(self) -> Path: return self.state_dir / "status.json"
    @property
    def log_file(self) -> Path: return self.state_dir / "backup-log.jsonl"
    @property
    def lock_file(self) -> Path: return self.base / "backup.lock"
    @property
    def remote_config(self) -> Path: return self.base / br.REMOTE_ENV_NAME
    @property
    def offsite_dir(self) -> Path: return self.state_dir / "offsite"

    def offsite_record(self, backup_id: str) -> Path: return self.offsite_dir / f"{backup_id}.json"

    def ensure(self) -> None:
        for directory in (self.base, self.staging, self.encrypted, self.state_dir):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)


# --- context (every side effect injectable, as in artesa_deploy) --------------------------------

@dataclass
class Context:
    root: Path
    rehearsal: bool = False
    guard: rp.SecretGuard = field(default_factory=rp.SecretGuard)
    runner: rp.Runner | None = None
    clock: Callable[[], datetime] = rc.utc_now
    sleep: Callable[[float], None] = time.sleep
    out: Callable[[str], None] = print
    age: str = "age"
    pg_bindir: str | None = None
    hostname: Callable[[], str] = socket.gethostname
    deploy_lock_wait: float = DEPLOY_LOCK_WAIT
    restore_target: Callable[..., object] | None = None   # tests: a fake disposable target
    remote_store: Callable[[br.RemoteConfig], object] | None = None   # tests: a fake bucket (default: br.make_store)
    http: br.Transport = br.https_transport                           # dead-man's switch ping

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if self.runner is None:
            self.runner = rp.Runner(self.guard)
        else:
            self.guard = self.runner.guard

    def say(self, text: str = "") -> None:
        self.out(self.guard.scrub(text))


# --- configuration ------------------------------------------------------------------------------

def read_recipients(path: Path) -> list[str]:
    """K1 and K2: two distinct PUBLIC age X25519 recipients from
    ``shared/backup/backup.env`` (regular file, 0600, owned by the service
    user). Anything that looks like a private key is refused outright."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise rc.OpsError(rc.Exit.CONFIG, "shared/backup/backup.env not found: K1/K2 age recipients are required (no unencrypted fallback)") from None
    if not stat.S_ISREG(st.st_mode):
        raise rc.OpsError(rc.Exit.CONFIG, "shared/backup/backup.env must be a regular file, not a symlink")
    if st.st_mode & 0o077:
        raise rc.OpsError(rc.Exit.CONFIG, f"shared/backup/backup.env mode is {stat.S_IMODE(st.st_mode):04o}; it must be 0600")
    if st.st_uid != os.geteuid():
        raise rc.OpsError(rc.Exit.CONFIG, "shared/backup/backup.env must be owned by the service user")
    text = path.read_text(encoding="utf-8", errors="replace")
    if "AGE-SECRET-KEY-" in text.upper():
        raise rc.OpsError(rc.Exit.CONFIG, "shared/backup/backup.env contains an age PRIVATE key; private keys must never be on this host")
    values = rp.parse_env_text(text)
    recipients = []
    for key in RECIPIENT_KEYS:
        value = values.get(key, "").strip()
        if not value:
            raise rc.OpsError(rc.Exit.CONFIG, f"{key} is missing: both K1 and K2 are required (no unencrypted fallback)")
        if not _RECIPIENT_RE.fullmatch(value):
            raise rc.OpsError(rc.Exit.CONFIG, f"{key} is not an age X25519 public recipient (age1...)")
        recipients.append(value)
    if recipients[0] == recipients[1]:
        raise rc.OpsError(rc.Exit.CONFIG, "K1 and K2 must be different recipients")
    return recipients


# --- state and log --------------------------------------------------------------------------------

def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_state(layout: BackupLayout) -> dict:
    try:
        data = json.loads(layout.status_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def new_state() -> dict:
    return {"schema_version": STATE_SCHEMA, "tool_version": rc.TOOL_VERSION, "last_attempt_at": None, "last_result": None,
            "last_success_at": None, "last_backup_id": None, "last_encrypted_sha256": None, "last_restore_check": None,
            "consecutive_failures": 0, "last_error": None, "encryption": None, "offsite": {"status": "not-configured"},
            "stale_after_hours": STALE_AFTER_HOURS, "local_backups": 0, "retention_warning": None}


class BackupLog:
    """Append-only JSON Lines, 0600, allowlisted keys, every line scanned for
    secret values (same rules as the deploy log)."""

    def __init__(self, layout: BackupLayout, guard: rp.SecretGuard, run_id: str, clock) -> None:
        self.layout, self.guard, self.run_id, self.clock = layout, guard, run_id, clock

    def event(self, event: str, **fields: object) -> None:
        record = {"ts": rc.utc_iso(self.clock()), "run_id": self.run_id, "event": event, "tool_version": rc.TOOL_VERSION, **fields}
        unknown = set(record) - ALLOWED_LOG_KEYS
        if unknown:
            raise rc.OpsError(rc.Exit.INTERNAL, f"backup log refuses unknown field(s): {sorted(unknown)}")
        line = json.dumps(record, sort_keys=True, ensure_ascii=True)
        if self.guard.contains(line):
            raise rc.OpsError(rc.Exit.INTERNAL, "backup log refused an entry containing a secret value")
        fd = os.open(self.layout.log_file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)


# --- age -----------------------------------------------------------------------------------------------

def age_header_problems(path: Path, expected_recipients: int = 2) -> list[str]:
    """What can be checked WITHOUT a private key: the age v1 magic line, one
    X25519 stanza per recipient, the MAC line and a non-empty payload. It
    does not (cannot) prove the payload decrypts."""
    try:
        size = os.stat(path).st_size
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return ["encrypted bundle is missing or unreadable"]
    if not head.startswith(AGE_MAGIC):
        return ["not an age v1 file (bad magic line)"]
    lines = head.split(b"\n")
    stanzas = [ln for ln in lines if ln.startswith(b"-> ")]
    x25519 = [ln for ln in stanzas if _X25519_STANZA.match(ln)]
    problems = []
    if len(x25519) != expected_recipients or len(stanzas) != expected_recipients:
        problems.append(f"age header has {len(x25519)} X25519 recipient stanza(s) (of {len(stanzas)}); expected {expected_recipients}")
    mac = next((i for i, ln in enumerate(lines) if ln.startswith(b"--- ")), None)
    if mac is None:
        problems.append("age header has no MAC line")
    else:
        header_len = sum(len(ln) + 1 for ln in lines[: mac + 1])
        if size <= header_len:
            problems.append("age file has no payload")
    return problems


# --- tool ----------------------------------------------------------------------------------------------

class Tool:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.layout = dl.Layout(ctx.root)
        self.blayout = BackupLayout(ctx.root)
        self.run_id = f"b-{ctx.clock().strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
        self._log: BackupLog | None = None
        self._remote_meta: dict = {"status": "not-configured"}

    def log(self) -> BackupLog:
        if self._log is None:
            self._log = BackupLog(self.blayout, self.ctx.guard, self.run_id, self.ctx.clock)
        return self._log

    def check_root(self) -> None:
        production_root = Path(os.path.realpath(rc.DEFAULT_ROOT))
        here = Path(os.path.realpath(self.ctx.root))
        if self.ctx.rehearsal and here == production_root:
            raise rc.OpsError(rc.Exit.USAGE, "--rehearsal cannot be used with the production root")
        if not self.ctx.rehearsal and here != production_root:
            raise rc.OpsError(rc.Exit.USAGE, "a non-default --root is only allowed together with --rehearsal")
        if not self.layout.root.is_dir():
            raise rc.OpsError(rc.Exit.PREFLIGHT, "the deployment root does not exist")

    # -- run ------------------------------------------------------------------------------------------------

    @contextmanager
    def backup_lock(self) -> Iterator[None]:
        """flock on shared/backup/backup.lock, independent of the deploy lock;
        the kernel drops it if the process dies (no stale lock to clean)."""
        self.blayout.ensure()
        fd = os.open(self.blayout.lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise rc.OpsError(rc.Exit.PREFLIGHT, "another artesa-backup operation holds the backup lock") from None
            os.ftruncate(fd, 0)
            os.write(fd, f"pid={os.getpid()}\n".encode())
            yield
        finally:
            os.close(fd)  # releases the flock

    def cmd_run(self) -> int:
        self.check_root()
        try:
            with self.backup_lock():
                return self._run_locked()
        except rc.OpsError as exc:
            if "holds the backup lock" not in exc.message:
                raise
            self.ctx.say("another artesa-backup operation holds the backup lock; not starting a second run")
            self._safe_event("run_locked", result="skipped", exit_code=int(rc.Exit.PREFLIGHT))
            return int(rc.Exit.PREFLIGHT)

    def _run_locked(self) -> int:
        state = {**new_state(), **read_state(self.blayout)}
        state["last_attempt_at"] = rc.utc_iso(self.ctx.clock())
        staging: Path | None = None
        try:
            removed = self._clear_stale_staging()
            if removed:
                self.ctx.say(f"removed {len(removed)} leftover staging director(y/ies) of an interrupted run (they could hold plaintext)")
            recipients = read_recipients(self.blayout.config)
            state["encryption"] = {"algorithm": "age", "recipients": recipients}
            self._check_age()
            remote_cfg, remote_error = self._remote_config()
            env = rp.read_env_file(self.layout.env_file, enforce_mode=True)
            self.ctx.guard.merge(env.guard)
            if not env.values.get("DATABASE_URL"):
                raise rc.OpsError(rc.Exit.CONFIG, "shared/.env has no DATABASE_URL")
            current = dl.read_link(self.layout, self.layout.current)
            if not current:
                raise rc.OpsError(rc.Exit.PREFLIGHT, "there is no current release to run the database probe with")
            release = self._release(current)
            commit = release["git"]["commit"] if release else None
            db = ddb.read_db_state(self.ctx.runner, self.layout.release_dir(current), rp.child_env(env.values))
            backup_id = f"{self.ctx.clock().strftime('%Y%m%dT%H%M%SZ')}-{commit[:12] if commit else 'nocommit'}"
            if (self.blayout.encrypted / backup_id).exists():
                raise rc.OpsError(rc.Exit.BACKUP, f"a backup with id {backup_id} already exists")
            staging = self.blayout.staging / f"{backup_id}.tmp-{os.getpid()}"
            os.mkdir(staging, 0o700)
            self.log().event("run_start", backup_id=backup_id, release_id=current, git_sha=(commit or "")[:12] or None, alembic=db.revision)
            result = self._pipeline(backup_id, staging, env, db, current, commit, release, recipients)
            staging = None
            state.update({"last_result": "success", "last_success_at": rc.utc_iso(self.ctx.clock()), "last_backup_id": backup_id,
                          "last_encrypted_sha256": result["encrypted_sha256"], "last_restore_check": result["restore_check"],
                          "consecutive_failures": 0, "last_error": None})
            offsite_ok = self._offsite(state, remote_cfg, remote_error)
            state["retention_warning"] = None
            try:
                # with off-host copies configured (even if misconfigured), a backup without a verified remote copy is kept
                deleted = self._retention(require_offsite_verified=remote_cfg is not None or remote_error is not None)
                if deleted:
                    self.ctx.say(f"retention: removed {len(deleted)} older encrypted backup(s): {', '.join(deleted)}")
                    self._safe_event("retention", removed=len(deleted))
            except (rc.OpsError, OSError) as exc:  # never destroys the backup just made; reported, not fatal
                state["retention_warning"] = self.ctx.guard.scrub(getattr(exc, "message", type(exc).__name__))[:200]
                self.ctx.say(f"WARNING: local retention failed ({state['retention_warning']}); the new backup is kept")
            state["local_backups"] = len(self._backup_dirs())
            _write_json_atomic(self.blayout.status_file, state)
            self.log().event("run_ok", backup_id=backup_id, result="success", exit_code=0,
                             encrypted_sha256=result["encrypted_sha256"], encrypted_size=result["encrypted_size"])
            self.ctx.say(f"BACKUP OK {backup_id}: encrypted to K1+K2 ({result['encrypted_size']} bytes, sha256 {result['encrypted_sha256'][:16]}...), "
                         f"restore-check PASS, no plaintext left")
            if remote_cfg is None and remote_error is None:
                self.ctx.say("OFFSITE: NOT CONFIGURED -- D10: INCOMPLETE (off-host copies are phase D10.2)")
                return 0
            if not offsite_ok:
                self.ctx.say(f"OFFSITE: FAILED -- the local encrypted backup {backup_id} is kept; the upload is retried on the next run. D10: INCOMPLETE")
                return int(rc.Exit.BACKUP)
            self.ctx.say(f"OFFSITE: VERIFIED ({state['offsite']['provider']}, bucket {state['offsite']['bucket']}) -- D10: INCOMPLETE (recovery drills: D10.3)")
            if remote_cfg is not None and remote_cfg.deadman_url:
                pinged = br.ping_deadman(remote_cfg.deadman_url, transport=self.ctx.http, rehearsal=self.ctx.rehearsal)
                state["offsite"]["deadman_last_ping"] = {"at": rc.utc_iso(self.ctx.clock()), "ok": pinged}
                _write_json_atomic(self.blayout.status_file, state)
                if not pinged:
                    self.ctx.say("WARNING: the dead-man's switch ping failed; the external monitor will alert if this keeps happening")
            return 0
        except rc.OpsError as exc:
            return self._fail(state, exc.code, exc.message, staging, result_name="deferred" if getattr(exc, "deferred", False) else "failure")
        except (KeyboardInterrupt, EOFError):
            return self._fail(state, int(rc.Exit.NO_TTY_OR_ABORT), "interrupted", staging)
        except Exception as exc:  # noqa: BLE001 -- never a traceback: locals may carry secrets
            return self._fail(state, int(rc.Exit.INTERNAL), f"internal error: {type(exc).__name__}", staging)

    def _remote_config(self) -> tuple[br.RemoteConfig | None, rc.OpsError | None]:
        """remote.env is optional. A broken one does NOT stop the local
        backup (it is still worth having); it makes the off-host step fail."""
        try:
            cfg = br.read_remote_config(self.blayout.remote_config, rehearsal=self.ctx.rehearsal)
        except rc.OpsError as exc:
            self._remote_meta = {"status": "misconfigured"}
            return None, exc
        if cfg is None:
            self._remote_meta = {"status": "not-configured"}
            return None, None
        self.ctx.guard.add(*cfg.secrets())
        self._remote_meta = {"status": "offsite-pending", "provider": cfg.provider, "bucket": cfg.bucket, "prefix": cfg.prefix}
        return cfg, None

    def _offsite(self, state: dict, cfg: br.RemoteConfig | None, error: rc.OpsError | None) -> bool:
        """Upload + verify every local backup that has no verified remote copy
        yet (newest first; a failed upload is retried by the next run, the
        object names are idempotent). Never deletes anything remotely."""
        previous = state.get("offsite") if isinstance(state.get("offsite"), dict) else {}
        if cfg is None and error is None:
            state["offsite"] = {"status": "not-configured"}
            return True
        info = {"provider": cfg.provider if cfg else None, "bucket": cfg.bucket if cfg else None, "prefix": cfg.prefix if cfg else None,
                "last_verified_backup_id": previous.get("last_verified_backup_id"), "last_verified_at": previous.get("last_verified_at")}
        try:
            if error is not None:
                raise error
            store = (self.ctx.remote_store or br.make_store)(cfg)
            store.authorize()
            warnings = list(getattr(store, "warnings", []))
            done = []
            for directory in self._backup_dirs():
                if self._offsite_verified(directory.name):
                    continue
                meta = json.loads((directory / META_NAME).read_text(encoding="utf-8"))
                created = datetime.fromisoformat(str(meta["created_at"]).replace("Z", "+00:00"))
                record = br.upload_backup(store, directory, directory.name, created, (BUNDLE_NAME, META_NAME),
                                          lambda: rc.utc_iso(self.ctx.clock()))
                self.blayout.offsite_dir.mkdir(mode=0o700, exist_ok=True)
                _write_json_atomic(self.blayout.offsite_record(directory.name), record)
                done.append(directory.name)
                self._safe_event("offsite_ok", backup_id=directory.name, result="verified", provider=cfg.provider,
                                 objects=len(record["objects"]), uploaded=sum(o["uploaded"] for o in record["objects"]))
            newest = next((d.name for d in self._backup_dirs() if self._offsite_verified(d.name)), None)
            info.update({"status": "verified", "last_verified_backup_id": newest or info["last_verified_backup_id"],
                         "last_verified_at": rc.utc_iso(self.ctx.clock()) if done or newest else info["last_verified_at"],
                         "consecutive_failures": 0, "last_error": None, "warnings": [self.ctx.guard.scrub(w) for w in warnings]})
            state["offsite"] = info
            for name in done:
                self.ctx.say(f"offsite: {name} uploaded and verified ({cfg.provider}, bucket {cfg.bucket})")
            for warning in info["warnings"]:
                self.ctx.say(f"WARNING: offsite: {warning}")
            return True
        except (rc.OpsError, OSError, ValueError, KeyError) as exc:
            text = self.ctx.guard.scrub(getattr(exc, "message", None) or f"{type(exc).__name__}")[:300]
            info.update({"status": "failed", "consecutive_failures": int(previous.get("consecutive_failures") or 0) + 1,
                         "last_error": {"code": int(getattr(exc, "code", rc.Exit.BACKUP)), "message": text}})
            state["offsite"] = info
            self._safe_event("offsite_failed", result="failure", exit_code=int(rc.Exit.BACKUP), detail=text[:200])
            self.ctx.say(f"OFFSITE FAILED: {text}")
            return False

    def _offsite_verified(self, backup_id: str) -> bool:
        try:
            record = json.loads(self.blayout.offsite_record(backup_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(record, dict) and record.get("status") == "verified" and record.get("backup_id") == backup_id

    def _fail(self, state: dict, code: int, message: str, staging: Path | None, result_name: str = "failure") -> int:
        self._discard_staging(staging)
        text = self.ctx.guard.scrub(message)[:300]
        state.update({"last_result": result_name, "consecutive_failures": int(state.get("consecutive_failures") or 0) + 1,
                      "last_error": {"code": int(code), "message": text}})
        try:
            state["local_backups"] = len(self._backup_dirs())
            _write_json_atomic(self.blayout.status_file, state)
        except OSError:
            pass
        self._safe_event("run_failed", result=result_name, exit_code=int(code), detail=text[:200])
        self.ctx.say(f"BACKUP FAILED ({result_name}): {text}")
        self.ctx.say(f"  consecutive failures: {state['consecutive_failures']}; no plaintext was kept")
        return int(code)

    def _safe_event(self, event: str, **fields: object) -> None:
        try:
            self.log().event(event, **fields)
        except (rc.OpsError, OSError):
            pass

    def _check_age(self) -> None:
        result = self.ctx.runner.run([self.ctx.age, "--version"], env=_tool_env(), timeout=15)
        if result.returncode != 0:
            raise rc.OpsError(rc.Exit.CONFIG, "the 'age' binary is not available; refusing to back up without encryption")

    def _release(self, release_id: str) -> dict | None:
        try:
            return rc.validate_release_json(json.loads((self.layout.release_dir(release_id) / "RELEASE.json").read_text(encoding="utf-8")),
                                            expected_id=release_id)
        except (OSError, ValueError, rc.OpsError):
            return None

    def _pipeline(self, backup_id: str, staging: Path, env: rp.EnvFile, db: ddb.DbState, current: str, commit: str | None,
                  release: dict | None, recipients: list[str]) -> dict:
        dump = staging / "database.dump"
        digest, dump_text = self._dump_under_deploy_lock(env, db, dump)
        size = os.stat(dump).st_size
        # restore-check of the plaintext, BEFORE it is encrypted and unlinked (D10 decision 7)
        sidecar = dump.with_name(dump.name + ".json")
        _write_private(sidecar, json.dumps({"dump": dump.name, "sha256": digest, "size_bytes": size, "alembic_revision": db.revision,
                                            "row_counts": dict(sorted(db.row_counts.items())), "server_major": db.server_major}) + "\n")
        try:
            report = self._restore_check(dump, current, env, db.server_major)
        except rc.OpsError as exc:
            raise rc.OpsError(rc.Exit.BACKUP, f"restore-check of the fresh dump failed ({exc.message}); no backup was kept") from None
        restore = {"at": rc.utc_iso(self.ctx.clock()), "ok": report.ok, "target": report.target_kind, "tables": report.tables,
                   "alembic_revision": report.alembic_revision, "counts_match": report.counts_match, "target_destroyed": report.cleanup_ok}
        if not report.ok:
            raise rc.OpsError(rc.Exit.BACKUP, "restore-check of the fresh dump failed (" + "; ".join(report.problems[:2]) + "); no backup was kept")
        manifest = {
            "schema_version": MANIFEST_SCHEMA, "backup_id": backup_id, "created_at": rc.utc_iso(self.ctx.clock()), "hostname": self.ctx.hostname(),
            "backup_tool_version": rc.TOOL_VERSION,
            "application": {"release_id": current, "git_commit": commit},
            "db": {"engine": "postgresql", "server_version": db.server_version_num, "server_major": db.server_major, "pg_dump_version": dump_text,
                   "dump_format": "custom (pg_dump -Fc --no-owner --no-privileges)", "alembic_revision": db.revision,
                   "table_counts": dict(sorted(db.row_counts.items())), "roles_and_grants": "not included (recreate per docs/BACKUP.md)"},
            "files": {"database.dump": {"size": size, "sha256": digest}},
            "restore_check": restore,
            "encryption": {"algorithm": "age", "recipient_type": "X25519", "recipients": recipients},
            "remote": dict(self._remote_meta),
        }
        _write_private(staging / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        bundle = staging / "bundle.tar"
        self._make_bundle(bundle, staging, current)
        encrypted = staging / BUNDLE_NAME
        result = self.ctx.runner.run([self.ctx.age, "--encrypt", "-r", recipients[0], "-r", recipients[1], "-o", str(encrypted), str(bundle)],
                                     env=_tool_env(), timeout=AGE_TIMEOUT)
        if result.returncode != 0:
            raise rc.OpsError(rc.Exit.BACKUP, f"age encryption failed (exit {result.returncode}); no backup was kept")
        os.chmod(encrypted, 0o600)
        problems = age_header_problems(encrypted, expected_recipients=2)
        if problems:
            raise rc.OpsError(rc.Exit.BACKUP, "the encrypted bundle is not valid: " + "; ".join(problems))
        enc_sha, enc_size = rc.sha256_file(str(encrypted)), os.stat(encrypted).st_size
        meta = {"schema_version": MANIFEST_SCHEMA, "backup_id": backup_id, "created_at": manifest["created_at"], "backup_tool_version": rc.TOOL_VERSION,
                "encrypted": {"name": BUNDLE_NAME, "size": enc_size, "sha256": enc_sha},
                "encryption": manifest["encryption"], "restore_check": {"ok": True, "at": restore["at"]},
                "remote": dict(self._remote_meta),
                "note": "public metadata only; the full manifest (db, application, plaintext checksum) is inside the encrypted bundle"}
        partial = self.blayout.encrypted / f".{backup_id}.partial"
        os.mkdir(partial, 0o700)
        try:
            os.rename(encrypted, partial / BUNDLE_NAME)
            _write_private(partial / META_NAME, json.dumps(meta, indent=2, sort_keys=True) + "\n")
            os.rename(partial, self.blayout.encrypted / backup_id)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        # zero persistent plaintext: dump, sidecar, manifest and tar are unlinked now, then checked gone
        self._discard_staging(staging)
        leftovers = [p.name for p in self.blayout.staging.iterdir()] if self.blayout.staging.exists() else []
        final = sorted(p.name for p in (self.blayout.encrypted / backup_id).iterdir())
        if leftovers or final != sorted([BUNDLE_NAME, META_NAME]):
            raise rc.OpsError(rc.Exit.INTERNAL, "plaintext check failed after the backup; inspect shared/backup/ by hand")
        return {"encrypted_sha256": enc_sha, "encrypted_size": enc_size, "restore_check": restore}

    def _dump_under_deploy_lock(self, env: rp.EnvFile, db: ddb.DbState, dump: Path) -> tuple[str, str]:
        """The deploy lock is held only while pg_dump runs (seconds). Order is
        always backup lock -> deploy lock; deploy never takes the backup lock,
        so the two can never deadlock. A busy deploy lock is waited for, up to
        ``deploy_lock_wait``; then the run is recorded as 'deferred'."""
        deadline = time.monotonic() + self.ctx.deploy_lock_wait
        while True:
            try:
                with dl.deploy_lock(self.layout):
                    return ddb.dump_verified(self.ctx.runner, env.values["DATABASE_URL"], db, dump)
            except rc.OpsError as exc:
                if "holds the deployment lock" not in exc.message:
                    raise
                if time.monotonic() >= deadline:
                    error = rc.OpsError(rc.Exit.PREFLIGHT, f"a deploy operation held the deployment lock for more than {self.ctx.deploy_lock_wait:.0f} s; backup deferred")
                    error.deferred = True  # type: ignore[attr-defined]
                    raise error from None
                self.ctx.say(f"deploy lock busy; waiting {DEPLOY_LOCK_POLL:.0f} s")
                self.ctx.sleep(DEPLOY_LOCK_POLL)

    def _restore_check(self, dump: Path, current: str, env: rp.EnvFile, server_major: int):
        if self.ctx.restore_target is not None:
            target = self.ctx.restore_target(dump.parent / "restore")
        else:
            bindir = Path(self.ctx.pg_bindir) if self.ctx.pg_bindir else ddb.default_pg_bindir(server_major)
            target = ddb.EphemeralCluster(runner=self.ctx.runner, bindir=bindir, parent=dump.parent / "restore", min_major=server_major)
        return ddb.restore_check(runner=self.ctx.runner, dump=dump, target=target, probe_release_dir=self.layout.release_dir(current),
                                 app_env=env.values)

    def _make_bundle(self, bundle: Path, staging: Path, current: str) -> None:
        """Plain tar (the dump is already compressed): the dump, the full
        manifest and recovery metadata (RELEASE.json of the active release,
        bin/TOOL.json, the deploy log). No secrets: never shared/.env."""
        members = [(staging / "database.dump", "database.dump"), (staging / "manifest.json", "manifest.json")]
        for source, name in ((self.layout.release_dir(current) / "RELEASE.json", "recovery/RELEASE.json"),
                             (self.layout.bin / "TOOL.json", "recovery/TOOL.json"),
                             (self.layout.deploy_log, "recovery/deploy-log.jsonl")):
            if source.is_file() and not source.is_symlink():
                members.append((source, name))
        fd = os.open(bundle, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle, tarfile.open(fileobj=handle, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for source, name in members:
                info = tar.gettarinfo(str(source), arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o600
                with open(source, "rb") as src:
                    tar.addfile(info, src)

    def _discard_staging(self, staging: Path | None) -> None:
        if staging is not None and staging.exists():
            _remove_tree(staging)

    def _clear_stale_staging(self) -> list[str]:
        """Leftovers of a killed run (they may hold plaintext). Safe: the
        backup lock is held, so no live run owns them."""
        removed = []
        for entry in list(self.blayout.staging.iterdir()) if self.blayout.staging.exists() else []:
            if entry.is_dir() and not entry.is_symlink():
                _remove_tree(entry)
            else:
                entry.unlink()
            removed.append(entry.name)
        for entry in list(self.blayout.encrypted.iterdir()) if self.blayout.encrypted.exists() else []:
            if entry.name.startswith(".") and entry.name.endswith(".partial") and entry.is_dir() and not entry.is_symlink():
                _remove_tree(entry)
                removed.append(entry.name)
        return removed

    # -- retention --------------------------------------------------------------------------------------------

    def _backup_dirs(self) -> list[Path]:
        """Only directories this tool created (name = backup id), newest first.
        Anything else in encrypted/ is never touched."""
        if not self.blayout.encrypted.exists():
            return []
        found = [p for p in self.blayout.encrypted.iterdir() if p.is_dir() and not p.is_symlink() and _BACKUP_ID_RE.fullmatch(p.name)]
        return sorted(found, key=lambda p: p.name, reverse=True)

    def verify_problems(self, directory: Path) -> list[str]:
        meta_path, bundle = directory / META_NAME, directory / BUNDLE_NAME
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ["meta.json missing or unreadable"]
        problems = []
        if not isinstance(meta, dict) or meta.get("schema_version") != MANIFEST_SCHEMA or meta.get("backup_id") != directory.name:
            return ["meta.json does not describe this backup"]
        enc = meta.get("encrypted") or {}
        try:
            if os.stat(bundle).st_size != enc.get("size"):
                problems.append("encrypted bundle size differs from meta.json")
            elif rc.sha256_file(str(bundle)) != enc.get("sha256"):
                problems.append("encrypted bundle sha256 differs from meta.json")
        except OSError:
            return ["encrypted bundle missing"]
        problems += age_header_problems(bundle, expected_recipients=len((meta.get("encryption") or {}).get("recipients") or []) or 2)
        for path in (directory, meta_path, bundle):
            if stat.S_IMODE(os.stat(path).st_mode) & 0o077:
                problems.append(f"{path.name} is readable by others")
        return problems

    def _retention(self, keep: int = KEEP_LOCAL, require_offsite_verified: bool = False) -> list[str]:
        """Keep the newest ``keep`` encrypted backups. Never deletes the newest,
        never the last one that verifies, never an unknown directory, never
        shared/backups/. With D10.2, a backup without a verified remote copy
        is kept too (``require_offsite_verified``)."""
        dirs = self._backup_dirs()
        if len(dirs) <= keep:
            return []
        kept = dirs[:keep]
        if not any(not self.verify_problems(d) for d in kept):
            last_valid = next((d for d in dirs[keep:] if not self.verify_problems(d)), None)
            if last_valid is not None:
                kept.append(last_valid)
        deleted = []
        for directory in dirs[keep:]:
            if directory in kept:
                continue
            if require_offsite_verified and not self._offsite_verified(directory.name):
                continue
            if Path(os.path.realpath(directory)).parent != Path(os.path.realpath(self.blayout.encrypted)):
                continue
            _remove_tree(directory)
            self.blayout.offsite_record(directory.name).unlink(missing_ok=True)
            deleted.append(directory.name)
        return deleted

    # -- status / verify / restore-test ---------------------------------------------------------------------------

    def status_report(self) -> dict:
        state = {**new_state(), **read_state(self.blayout)}
        now = self.ctx.clock()
        age_hours = None
        if state.get("last_success_at"):
            try:
                age_hours = round((now - datetime.fromisoformat(state["last_success_at"].replace("Z", "+00:00"))).total_seconds() / 3600, 1)
            except ValueError:
                age_hours = None
        stale = age_hours is None or age_hours > STALE_AFTER_HOURS
        offsite = state.get("offsite") if isinstance(state.get("offsite"), dict) else {}
        configured = self.blayout.remote_config.exists()   # read-only: existence only, no network
        offsite_status = offsite.get("status", "not-configured")
        if configured and offsite_status == "not-configured":
            offsite_status = "pending (configured, no run since)"
        elif not configured and offsite_status != "not-configured":
            offsite_status = "not-configured (remote.env removed)"
        offsite_age = None
        if configured and offsite.get("last_verified_at"):
            try:
                offsite_age = round((now - datetime.fromisoformat(offsite["last_verified_at"].replace("Z", "+00:00"))).total_seconds() / 3600, 1)
            except ValueError:
                offsite_age = None
        offsite_stale = configured and (offsite_age is None or offsite_age > STALE_AFTER_HOURS or offsite_status != "verified")
        return {**state, "last_success_age_hours": age_hours, "stale": stale, "local_backups": len(self._backup_dirs()),
                "encryption_status": "configured" if state.get("encryption") else "unknown (no run yet)",
                "offsite_configured": configured, "offsite_status": offsite_status, "offsite_age_hours": offsite_age,
                "offsite_stale": offsite_stale, "d10": "INCOMPLETE"}

    def cmd_status(self, as_json: bool) -> int:
        self.check_root()
        report = self.status_report()
        if as_json:
            self.ctx.say(json.dumps(report, indent=2, sort_keys=True))
        else:
            rcheck = report.get("last_restore_check") or {}
            self.ctx.say(f"last attempt      {report.get('last_attempt_at') or '-'}  ({report.get('last_result') or 'never run'})")
            self.ctx.say(f"last success      {report.get('last_success_at') or '-'}  backup {report.get('last_backup_id') or '-'}"
                         + (f"  age {report['last_success_age_hours']} h" if report.get("last_success_age_hours") is not None else ""))
            self.ctx.say(f"restore-check     {('PASS' if rcheck.get('ok') else 'FAIL') if rcheck else '-'} {rcheck.get('at') or ''}")
            self.ctx.say(f"failures in a row {report.get('consecutive_failures') or 0}" +
                         (f"  last error: {report['last_error']['message']}" if report.get("last_error") else ""))
            self.ctx.say(f"encryption        age K1+K2 ({report['encryption_status']})   local encrypted backups: {report['local_backups']}")
            if report.get("retention_warning"):
                self.ctx.say(f"WARNING: local retention: {report['retention_warning']}")
            if report["stale"]:
                self.ctx.say(f"STALE: no successful backup in the last {STALE_AFTER_HOURS} h")
            offsite = report.get("offsite") or {}
            if not report["offsite_configured"]:
                self.ctx.say("OFFSITE: NOT CONFIGURED")
                self.ctx.say("D10: INCOMPLETE (off-host copies: D10.2; recovery drills: D10.3)")
            else:
                self.ctx.say(f"OFFSITE: {report['offsite_status'].upper()}  {offsite.get('provider') or '-'} bucket {offsite.get('bucket') or '-'}  "
                             f"last verified {offsite.get('last_verified_backup_id') or '-'}"
                             + (f"  age {report['offsite_age_hours']} h" if report.get("offsite_age_hours") is not None else ""))
                if offsite.get("last_error"):
                    self.ctx.say(f"  offsite failures in a row {offsite.get('consecutive_failures') or 0}  last error: {offsite['last_error']['message']}")
                if (offsite.get("deadman_last_ping") or {}).get("ok") is False:
                    self.ctx.say("  WARNING: the last dead-man's switch ping failed")
                if report["offsite_stale"]:
                    self.ctx.say(f"OFFSITE STALE: no verified off-host copy in the last {STALE_AFTER_HOURS} h")
                self.ctx.say("D10: INCOMPLETE (recovery drills: D10.3)")
        healthy = not report["stale"] and not report.get("consecutive_failures") and not report["offsite_stale"]
        return 0 if healthy else int(rc.Exit.PREFLIGHT)

    def cmd_remote_check(self, ping_deadman: bool) -> int:
        """Read-only probe of the off-host destination (the D10.2 egress test):
        TLS reachability with full verification, the credential's real
        capabilities and restrictions (no-delete model). Uploads nothing."""
        self.check_root()
        cfg = br.read_remote_config(self.blayout.remote_config, rehearsal=self.ctx.rehearsal)
        if cfg is None:
            raise rc.OpsError(rc.Exit.CONFIG, "shared/backup/remote.env not found: off-host copies are not configured")
        self.ctx.guard.add(*cfg.secrets())
        store = (self.ctx.remote_store or br.make_store)(cfg)
        access = store.authorize()
        self.ctx.say(f"remote            {cfg.provider}  bucket {cfg.bucket}  prefix {cfg.prefix}")
        self.ctx.say(f"credential        capabilities: {', '.join(sorted(access.capabilities))}")
        self.ctx.say(f"restriction       bucket {access.bucket}  name prefix {access.name_prefix or '(none)'}")
        for warning in getattr(store, "warnings", []):
            self.ctx.say(f"WARNING: {warning}")
        self.ctx.say("no-delete model  OK: the credential cannot delete files, change buckets/keys or weaken Object Lock")
        code = 0
        # A writeFiles key can still HIDE a file (lifecycle may then expire it): re-list what was verified.
        missing, checked = [], 0
        for record_path in sorted(self.blayout.offsite_dir.glob("*.json")) if self.blayout.offsite_dir.is_dir() else []:
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for obj in record.get("objects") or []:
                checked += 1
                found = store.stat(obj["name"])
                if found is None or found.size != obj["size"] or found.sha1 != obj["sha1"]:
                    missing.append(obj["name"])
        if checked:
            self.ctx.say(f"remote copies     {checked - len(missing)}/{checked} verified objects still present and identical")
            for name in missing[:10]:
                self.ctx.say(f"  MISSING or CHANGED: {name}")
            if missing:
                code = int(rc.Exit.BACKUP)
        if ping_deadman:
            if not cfg.deadman_url:
                self.ctx.say("dead-man's switch: not configured (ARTESA_BACKUP_DEADMAN_URL)")
                code = int(rc.Exit.CONFIG)
            else:
                ok = br.ping_deadman(cfg.deadman_url, transport=self.ctx.http, rehearsal=self.ctx.rehearsal)
                self.ctx.say(f"dead-man's switch: test ping {'OK' if ok else 'FAILED'}")
                code = 0 if ok else int(rc.Exit.BACKUP)
        self.ctx.say(f"remote-check {'PASS' if code == 0 else 'FAIL'} (nothing was uploaded)")
        return code

    def cmd_verify(self, backup_id: str | None, all_: bool) -> int:
        self.check_root()
        dirs = self._backup_dirs()
        if backup_id:
            if not _BACKUP_ID_RE.fullmatch(backup_id):
                raise rc.OpsError(rc.Exit.USAGE, "not a backup id")
            dirs = [d for d in dirs if d.name == backup_id]
            if not dirs:
                raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, f"no encrypted backup {backup_id}")
        elif not all_:
            dirs = dirs[:1]
        if not dirs:
            raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, "there is no encrypted backup to verify")
        bad = 0
        for directory in dirs:
            problems = self.verify_problems(directory)
            self.ctx.say(f"  [{'PASS' if not problems else 'FAIL'}] {directory.name}" + (f": {'; '.join(problems)}" if problems else ""))
            bad += bool(problems)
        state = read_state(self.blayout)
        if state.get("last_backup_id") and not (self.blayout.encrypted / state["last_backup_id"]).is_dir():
            self.ctx.say(f"  [FAIL] state: last_backup_id {state['last_backup_id']} is not on disk")
            bad += 1
        self.ctx.say("verified without a private key: files, sizes, sha256, age header (2 recipients), modes. The payload itself is only proven by a restore (D10.3 drill).")
        return 0 if not bad else int(rc.Exit.ARTIFACT_INVALID)

    def cmd_restore_test(self, path: str) -> int:
        """Restore-check of an explicitly provided PLAINTEXT dump: an N-08
        dump with its .json sidecar, or a decrypted and extracted bundle
        directory (database.dump + manifest.json). Never decrypts: the
        production host has no private key (decrypt off-host, D10.3)."""
        self.check_root()
        current = dl.read_link(self.layout, self.layout.current)
        if not current:
            raise rc.OpsError(rc.Exit.PREFLIGHT, "there is no current release to run the database probe with")
        env = rp.read_env_file(self.layout.env_file, enforce_mode=True)
        self.ctx.guard.merge(env.guard)
        source = Path(path)
        with self.backup_lock():
            return self._restore_test_locked(source, current, env)

    def _restore_test_locked(self, source: Path, current: str, env: rp.EnvFile) -> int:
        work = self.blayout.staging / f"restore-test.tmp-{os.getpid()}"
        os.mkdir(work, 0o700)
        try:
            if source.is_dir():
                manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
                dump = work / "database.dump"
                os.symlink(os.path.realpath(source / "database.dump"), dump)
                db = manifest["db"]
                _write_private(dump.with_name(dump.name + ".json"), json.dumps({
                    "dump": dump.name, "sha256": manifest["files"]["database.dump"]["sha256"], "size_bytes": manifest["files"]["database.dump"]["size"],
                    "alembic_revision": db["alembic_revision"], "row_counts": db["table_counts"]}) + "\n")
                major = int(db.get("server_major") or 0)
            else:
                dump = source
                major = int(ddb.read_backup_meta(dump).get("server_major") or 0)
            report = self._restore_check(dump, current, env, major)
        except (OSError, ValueError, KeyError, TypeError):
            raise rc.OpsError(rc.Exit.BACKUP, "the given path is not a dump with a sidecar nor an extracted bundle directory") from None
        finally:
            _remove_tree(work)
        for problem in report.problems:
            self.ctx.say(f"  problem: {problem}")
        self.ctx.say(f"restore-test {'PASS' if report.ok else 'FAIL'}: {report.tables} tables, alembic {report.alembic_revision or 'none'}, "
                     f"counts {'identical' if report.counts_match else 'DIFFERENT'}, disposable target destroyed: {report.cleanup_ok}")
        return 0 if report.ok else int(rc.Exit.BACKUP)


# --- helpers ----------------------------------------------------------------------------------------------------

def _tool_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL")}


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(text)


def _remove_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    for sub, dirs, _files in os.walk(path):
        for d in dirs:
            try:
                os.chmod(os.path.join(sub, d), 0o700)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


# --- CLI -----------------------------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="artesa-backup", description="Scheduled encrypted backups of the ArtesaNFC database (#126 / D10).")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--rehearsal", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pg-bindir", default=None, help="PostgreSQL server programs for the restore-check (default /usr/lib/postgresql/<major>/bin)")
    parser.add_argument("--age", default="age", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="dump, restore-check, encrypt to K1+K2, keep 7 (no TTY needed; systemd)")
    p.add_argument("--scheduled", action="store_true", help="marks a timer run (behaviour is identical)")
    p = sub.add_parser("status", help="last attempt/success, restore-check, age, failures (read-only)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("verify", help="check encrypted backups without a private key")
    p.add_argument("backup_id", nargs="?")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("restore-test", help="restore-check of an explicit plaintext dump or extracted bundle (never decrypts)")
    p.add_argument("path")
    p = sub.add_parser("remote-check", help="read-only probe of the off-host destination and the no-delete credential (D10.2)")
    p.add_argument("--ping-deadman", action="store_true", help="also send one test ping to the dead-man's switch")
    return parser


def main(argv: list[str] | None = None, ctx: Context | None = None) -> int:
    args = build_parser().parse_args(argv)
    if ctx is None:
        ctx = Context(root=Path(args.root) if args.root else Path(rc.DEFAULT_ROOT), rehearsal=args.rehearsal, pg_bindir=args.pg_bindir, age=args.age)
    tool = Tool(ctx)
    try:
        if args.command == "run":
            return tool.cmd_run()
        if args.command == "status":
            return tool.cmd_status(args.json)
        if args.command == "verify":
            return tool.cmd_verify(args.backup_id, args.all)
        if args.command == "restore-test":
            return tool.cmd_restore_test(args.path)
        if args.command == "remote-check":
            return tool.cmd_remote_check(args.ping_deadman)
    except rc.OpsError as exc:
        ctx.say(f"error: {exc.message}")
        return exc.code
    except KeyboardInterrupt:
        ctx.say("interrupted")
        return int(rc.Exit.NO_TTY_OR_ABORT)
    except Exception as exc:  # noqa: BLE001 -- never print a traceback
        ctx.say(f"internal error: {type(exc).__name__} (details suppressed on purpose)")
        return int(rc.Exit.INTERNAL)
    return int(rc.Exit.USAGE)


if __name__ == "__main__":
    sys.exit(main())
