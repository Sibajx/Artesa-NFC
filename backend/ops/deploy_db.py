"""Database side of the N-08 deploy tool: migration planning from the ledger,
read-only state probes, PostgreSQL backups and restore rehearsal (ADR-027).

Forward-only: nothing here ever runs ``alembic downgrade``. Credentials reach
``pg_dump``/``pg_restore``/Alembic through the environment (PG* variables /
DATABASE_URL), never through argv.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import release_common as rc
import release_probe as rp
from deploy_layout import Layout

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_TOC_RE = re.compile(r"^\d+; \d+ \d+ (TABLE DATA|TABLE) (\S+) (\S+)")
_PG_VERSION_RE = re.compile(r"\(PostgreSQL\)\s+(\d+)(?:\.(\d+))?")


# --- state + migration plan -------------------------------------------------------

@dataclass
class DbState:
    server_version_num: int
    alembic_versions: list[str]
    row_counts: dict[str, int]

    @property
    def server_major(self) -> int:
        return self.server_version_num // 10000

    @property
    def revision(self) -> str | None:
        if len(self.alembic_versions) > 1:
            raise rc.OpsError(rc.Exit.ALEMBIC, "the database carries more than one Alembic revision (unsupported)")
        return self.alembic_versions[0] if self.alembic_versions else None


def read_db_state(runner: rp.Runner, release_dir: Path, env: dict[str, str]) -> DbState:
    """Run the release's own ``release_probe.py db-state`` in the release's
    own venv (psycopg is only guaranteed there). Read-only transaction."""
    python = release_dir / "venv" / "bin" / "python"
    result = runner.run([str(python), str(release_dir / "ops" / "release_probe.py"), "db-state"], env=env, cwd=release_dir, timeout=60)
    if result.returncode != 0:
        reason = runner.guard.scrub((result.stderr or result.stdout).strip().splitlines()[-1:] and (result.stderr or result.stdout).strip().splitlines()[-1] or "no output")
        raise rc.OpsError(rc.Exit.ALEMBIC, f"cannot read the database state: {reason[:160]}")
    try:
        data = json.loads(result.stdout)
        return DbState(int(data["server_version_num"]), list(data["alembic_versions"]), {str(k): int(v) for k, v in data["row_counts"].items()})
    except (ValueError, KeyError, TypeError):
        raise rc.OpsError(rc.Exit.ALEMBIC, "database probe returned an unreadable answer") from None


@dataclass
class MigrationPlan:
    db_revision: str | None
    head: str
    pending: list[str] = field(default_factory=list)
    deployment_class: str = rc.CLASS_CODE_ONLY


def plan_migration(release: dict, ledger: dict[str, dict], state: DbState, *, other_known: set[str] | None = None) -> MigrationPlan:
    revisions = release["alembic"]["revisions"]
    head = release["alembic"]["head"]
    revision = state.revision
    try:
        pending = rc.pending_revisions(revisions, revision, head)
    except KeyError:
        if other_known and revision in other_known:
            raise rc.OpsError(rc.Exit.ALEMBIC, "the database is AHEAD of this release (it carries migrations this release does not have)") from None
        raise rc.OpsError(rc.Exit.ALEMBIC, "the database is at a revision unknown to this release") from None
    return MigrationPlan(revision, head, pending, rc.classify_pending(pending, ledger))


# --- backup -----------------------------------------------------------------------------

@dataclass
class BackupResult:
    path: Path
    sidecar: Path
    sha256: str
    size: int
    alembic_revision: str | None
    pg_dump_version: str


def pg_dump_major(runner: rp.Runner, tool: str = "pg_dump") -> tuple[int, str]:
    result = runner.run([tool, "--version"], env={k: v for k, v in os.environ.items() if k in ("PATH", "LANG")}, timeout=15)
    match = _PG_VERSION_RE.search(result.stdout)
    if result.returncode != 0 or not match:
        raise rc.OpsError(rc.Exit.BACKUP, f"{tool} is not available")
    return int(match.group(1)), result.stdout.strip().splitlines()[0]


def _tool_env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL")}
    env.update(extra)
    return env


def list_toc(runner: rp.Runner, dump: Path, tool: str = "pg_restore") -> list[tuple[str, str, str]]:
    result = runner.run([tool, "--list", str(dump)], env=_tool_env({}), timeout=120)
    if result.returncode != 0:
        raise rc.OpsError(rc.Exit.BACKUP, "the dump is not a readable PostgreSQL custom-format archive")
    return [m.groups() for m in (_TOC_RE.match(line) for line in result.stdout.splitlines()) if m]


def verify_dump(runner: rp.Runner, dump: Path, tables: set[str], *, expect_sha: str | None = None) -> str:
    """Non-empty, checksum, readable TOC containing alembic_version and every
    table the live database had. Returns the sha256."""
    try:
        size = os.stat(dump).st_size
    except FileNotFoundError:
        raise rc.OpsError(rc.Exit.BACKUP, "dump file not found") from None
    if size == 0:
        raise rc.OpsError(rc.Exit.BACKUP, "the dump is empty")
    digest = rc.sha256_file(str(dump))
    if expect_sha is not None and digest != expect_sha:
        raise rc.OpsError(rc.Exit.BACKUP, "the dump does not match its recorded checksum")
    toc = list_toc(runner, dump)
    present = {name for kind, schema, name in toc if kind == "TABLE" and schema == "public"}
    missing = sorted(({"alembic_version"} | tables) - present)
    if missing:
        raise rc.OpsError(rc.Exit.BACKUP, "the dump is missing table(s): " + ", ".join(missing))
    return digest


def create_backup(
    *, runner: rp.Runner, layout: Layout, env_file: rp.EnvFile, state: DbState, active_release: str | None, clock,
    active_commit: str | None = None, target_release: str | None = None, deploy_id: str | None = None,
) -> BackupResult:
    """``pg_dump -Fc`` of the production database, taken BEFORE any migration.
    Credentials travel in PG* environment variables only. The dump is 0600,
    verified (non-empty, readable TOC with every live table) and accompanied
    by ``<dump>.json`` (metadata) and ``<dump>.sha256`` (``sha256sum -c``)."""
    dump_major, dump_text = pg_dump_major(runner)
    if dump_major < state.server_major:
        raise rc.OpsError(rc.Exit.BACKUP, f"pg_dump {dump_major} is older than the server ({state.server_major}); refusing a backup that may not restore")
    stamp = clock().strftime("%Y%m%dT%H%M%SZ")
    revision = state.revision
    suffix = f"-{active_commit[:12]}" if active_commit else ""
    final = layout.backups / f"artesa-nfc-{stamp}-{revision or 'empty'}{suffix}.dump"
    partial = final.with_name(f".{final.name}.partial")
    for path in (final, partial):
        if path.exists():
            raise rc.OpsError(rc.Exit.BACKUP, "a backup with this name already exists")
    env = _tool_env(rp.pg_env(env_file.values["DATABASE_URL"]))
    result = runner.run(["pg_dump", "-Fc", "--no-owner", "--no-privileges", f"--file={partial}"], env=env, timeout=1800)
    if result.returncode != 0:
        _discard(partial)
        detail = runner.guard.scrub((result.stderr.strip().splitlines() or ["no output"])[-1])[:160]
        raise rc.OpsError(rc.Exit.BACKUP, f"pg_dump failed (exit {result.returncode}): {detail}")
    try:
        os.chmod(partial, 0o600)
        digest = verify_dump(runner, partial, set(state.row_counts))
        os.rename(partial, final)
    except BaseException:
        _discard(partial)
        raise
    size = os.stat(final).st_size
    sidecar = final.with_name(final.name + ".json")
    meta = {
        "schema_version": 1,
        "created_at_utc": rc.utc_iso(clock()),
        "dump": final.name,
        "sha256": digest,
        "size_bytes": size,
        "pg_dump_version": dump_text,
        "server_major": state.server_major,
        "alembic_revision": revision,
        "active_release": active_release,
        "active_commit": active_commit,
        "target_release": target_release,
        "deploy_id": deploy_id,
        "row_counts": dict(sorted(state.row_counts.items())),
        "tool_version": rc.TOOL_VERSION,
    }
    fd = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    fd = os.open(final.with_name(final.name + ".sha256"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(f"{digest}  {final.name}\n")
    return BackupResult(final, sidecar, digest, size, revision, dump_text)


def _discard(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def read_backup_meta(dump: Path) -> dict:
    sidecar = dump.with_name(dump.name + ".json")
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise rc.OpsError(rc.Exit.BACKUP, "the dump has no readable .json sidecar") from None
    required = {"sha256", "size_bytes", "alembic_revision", "row_counts", "dump"}
    if not isinstance(meta, dict) or not required <= set(meta) or meta["dump"] != dump.name:
        raise rc.OpsError(rc.Exit.BACKUP, "the dump sidecar is malformed")
    return meta


# --- restore check --------------------------------------------------------------------------
#
# A backup only counts once it has been restored somewhere disposable and
# checked. Two disposable targets exist, and neither is ever the production
# database:
#
#   EphemeralCluster  a brand-new PostgreSQL cluster (initdb) in a private
#                     temp dir under shared/state, unix socket only (no TCP;
#                     the socket lives in a short 0700 dir under $TMPDIR
#                     because socket paths are limited to 107 bytes),
#                     SCRAM with a one-off random password, destroyed at the
#                     end. Runs as the service user: no sudo, no CREATEDB on
#                     the production role, the production cluster is never
#                     contacted. This is what `deploy` uses.
#   ScratchServer     an operator-supplied loopback PostgreSQL (a disposable
#                     container on a laptop or in CI). The tool creates a
#                     uniquely named database there and drops it afterwards;
#                     host:port must differ from production's.

RESTORE_ROLE = "artesa_restore"


@dataclass
class ScratchDb:
    url: str
    tool_env: dict[str, str]
    dbname: str
    kind: str


class EphemeralCluster:
    kind = "ephemeral-cluster"

    def __init__(self, *, runner: rp.Runner, bindir: Path, parent: Path, min_major: int) -> None:
        self.runner, self.bindir, self.parent, self.min_major = runner, Path(bindir), Path(parent), min_major
        self.workdir: Path | None = None
        self.sockdir: Path | None = None
        self.started = False

    def _tool(self, name: str) -> str:
        return str(self.bindir / name)

    def create(self) -> ScratchDb:
        major, _ = pg_dump_major(self.runner, self._tool("initdb"))
        if major < self.min_major:
            raise rc.OpsError(rc.Exit.BACKUP, f"initdb {major} is older than the production server ({self.min_major})")
        self.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.workdir = Path(tempfile.mkdtemp(prefix="rc-", dir=self.parent))
        os.chmod(self.workdir, 0o700)
        data = self.workdir / "data"
        self.sockdir = sock = Path(tempfile.mkdtemp(prefix="artesa-rc-"))
        os.chmod(sock, 0o700)
        if len(str(sock / ".s.PGSQL.5432").encode()) > 107:
            raise rc.OpsError(rc.Exit.BACKUP, "temporary socket path is too long (set TMPDIR to a short directory)")
        password = secrets.token_urlsafe(24)
        self.runner.guard.add(password)
        pwfile = self.workdir / "pw"
        fd = os.open(pwfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(password + "\n")
        try:
            result = self.runner.run(
                [self._tool("initdb"), "-D", str(data), "-U", RESTORE_ROLE, f"--pwfile={pwfile}",
                 "--auth-local=scram-sha-256", "--auth-host=reject", "-E", "UTF8", "--no-sync", "--no-instructions"],
                env=_tool_env({}), timeout=300,
            )
        finally:
            _discard(pwfile)
        if result.returncode != 0:
            raise rc.OpsError(rc.Exit.BACKUP, "initdb of the disposable restore cluster failed")
        options = f"-c listen_addresses='' -k {shlex.quote(str(sock))} -p 5432 -c fsync=off -c full_page_writes=off"
        result = self.runner.run(
            [self._tool("pg_ctl"), "-D", str(data), "-l", str(self.workdir / "server.log"), "-w", "-t", "120", "-o", options, "start"],
            env=_tool_env({}), timeout=180,
        )
        if result.returncode != 0:
            raise rc.OpsError(rc.Exit.BACKUP, "the disposable restore cluster did not start")
        self.started = True
        dbname = f"artesa_restore_test_{secrets.token_hex(4)}"
        env = _tool_env({"PGHOST": str(sock), "PGPORT": "5432", "PGUSER": RESTORE_ROLE, "PGPASSWORD": password})
        if self.runner.run([self._tool("createdb"), dbname], env=env, timeout=60).returncode != 0:
            raise rc.OpsError(rc.Exit.BACKUP, "could not create the database in the disposable restore cluster")
        url = f"postgresql://{RESTORE_ROLE}:{quote(password, safe='')}@/{dbname}?host={quote(str(sock), safe='')}&port=5432"
        self.runner.guard.add(url)
        return ScratchDb(url, env, dbname, self.kind)

    def destroy(self) -> bool:
        ok = True
        if self.workdir is None:
            if self.sockdir is not None:
                shutil.rmtree(self.sockdir, ignore_errors=True)
            return ok
        if self.started:
            stop = self.runner.run([self._tool("pg_ctl"), "-D", str(self.workdir / "data"), "-m", "immediate", "-w", "stop"], env=_tool_env({}), timeout=120)
            ok = stop.returncode == 0
        shutil.rmtree(self.workdir, ignore_errors=True)
        if self.sockdir is not None:
            shutil.rmtree(self.sockdir, ignore_errors=True)
        return ok and not self.workdir.exists() and not (self.sockdir and self.sockdir.exists())


class ScratchServer:
    kind = "scratch-server"

    def __init__(self, *, runner: rp.Runner, admin_url: str, production_url: str | None) -> None:
        self.runner, self.admin_url, self.production_url = runner, admin_url, production_url
        self.env: dict[str, str] | None = None
        self.dbname: str | None = None

    def create(self) -> ScratchDb:
        parts = assert_scratch_server(self.admin_url, self.production_url)
        self.runner.guard.add(self.admin_url, parts["password"])
        env = _tool_env(rp.pg_env(self.admin_url))
        env.pop("PGDATABASE", None)
        dbname = f"artesa_restore_test_{secrets.token_hex(4)}"
        if self.runner.run(["createdb", "--maintenance-db", parts["dbname"], dbname], env=env, timeout=60).returncode != 0:
            raise rc.OpsError(rc.Exit.BACKUP, "could not create a database on the scratch server")
        self.env, self.dbname = env, dbname
        split = urlsplit(self.admin_url)
        url = urlunsplit((split.scheme, split.netloc, "/" + dbname, split.query, ""))
        self.runner.guard.add(url)
        return ScratchDb(url, env, dbname, self.kind)

    def destroy(self) -> bool:
        if self.dbname is None or self.env is None:
            return True
        maintenance = rp.split_database_url(self.admin_url)["dbname"]
        result = self.runner.run(["dropdb", "--if-exists", "--maintenance-db", maintenance, self.dbname], env=self.env, timeout=120)
        return result.returncode == 0


def assert_scratch_server(admin_url: str, production_url: str | None) -> dict[str, str]:
    """A scratch server must be on loopback and must not be the production
    PostgreSQL (same host *and* port means same cluster: refused, whatever
    the database name)."""
    scratch = rp.split_database_url(admin_url)
    if scratch["host"] not in _LOOPBACK_HOSTS:
        raise rc.OpsError(rc.Exit.CONFIG, "the scratch server must be on loopback (localhost/127.0.0.1/::1)")
    if production_url:
        prod = rp.split_database_url(production_url)
        if prod["host"] in _LOOPBACK_HOSTS and prod["port"] == scratch["port"]:
            raise rc.OpsError(rc.Exit.CONFIG, "the scratch server is the production PostgreSQL (same port); refusing")
    return scratch


def default_pg_bindir(server_major: int) -> Path:
    return Path(f"/usr/lib/postgresql/{server_major}/bin")


@dataclass
class RestoreReport:
    dump_sha256: str
    alembic_revision: str | None
    counts_match: bool
    problems: list[str]
    target_kind: str = ""
    cleanup_ok: bool = True
    migration_rehearsal: str = "skipped"  # skipped | ok | failed
    tables: int = 0

    @property
    def ok(self) -> bool:
        return self.counts_match and not self.problems and self.cleanup_ok and self.migration_rehearsal != "failed"

    def evidence(self) -> dict:
        return {
            "ok": self.ok, "dump_sha256": self.dump_sha256, "alembic_revision": self.alembic_revision,
            "counts_match": self.counts_match, "tables": self.tables, "problems": self.problems,
            "target": self.target_kind, "target_destroyed": self.cleanup_ok, "migration_rehearsal": self.migration_rehearsal,
        }


def restore_check(
    *, runner: rp.Runner, dump: Path, target, probe_release_dir: Path, app_env: dict[str, str] | None = None,
    migrate_release_dir: Path | None = None, expect_head: str | None = None,
) -> RestoreReport:
    """dump -> fresh disposable database -> Alembic revision, table set and row
    counts compared with the backup's sidecar -> (optionally) the target
    release's ``alembic upgrade head`` rehearsed on the restored copy -> the
    disposable database is destroyed, whatever happened."""
    meta = read_backup_meta(dump)
    digest = verify_dump(runner, dump, set(meta["row_counts"]), expect_sha=meta["sha256"])
    report = RestoreReport(digest, meta["alembic_revision"], False, [], target.kind)
    try:
        scratch = target.create()
        probe_env = rp.child_env({**(app_env or {}), "DATABASE_URL": scratch.url})
        before = read_db_state(runner, probe_release_dir, probe_env)
        if before.row_counts:
            raise rc.OpsError(rc.Exit.BACKUP, "the scratch database is not empty; refusing to restore into it")
        result = runner.run(
            ["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", f"--dbname={scratch.dbname}", str(dump)],
            env=scratch.tool_env, timeout=1800,
        )
        if result.returncode != 0:
            detail = runner.guard.scrub((result.stderr.strip().splitlines() or ["no output"])[-1])[:160]
            raise rc.OpsError(rc.Exit.BACKUP, f"pg_restore failed (exit {result.returncode}): {detail}")
        after = read_db_state(runner, probe_release_dir, probe_env)
        report.tables = len(after.row_counts)
        expected_rev = [meta["alembic_revision"]] if meta["alembic_revision"] else []
        if after.alembic_versions != expected_rev:
            report.problems.append("restored Alembic revision differs from the backup's")
        missing = sorted(set(meta["row_counts"]) - set(after.row_counts))
        if missing:
            report.problems.append("restored database lacks table(s): " + ", ".join(missing))
        report.counts_match = after.row_counts == meta["row_counts"]
        if not report.counts_match:
            differing = sorted(t for t in set(after.row_counts) | set(meta["row_counts"]) if after.row_counts.get(t) != meta["row_counts"].get(t))
            report.problems.append("row counts differ from the backup for table(s): " + ", ".join(differing))
        if migrate_release_dir is not None and not report.problems:
            python = str(Path(migrate_release_dir) / "venv" / "bin" / "python")
            upgrade = runner.run([python, "-m", "alembic", "upgrade", "head"], env=probe_env, cwd=migrate_release_dir, timeout=900)
            migrated = read_db_state(runner, probe_release_dir, probe_env) if upgrade.returncode == 0 else None
            if migrated is None or (expect_head is not None and migrated.alembic_versions != [expect_head]):
                report.migration_rehearsal = "failed"
                report.problems.append("alembic upgrade head failed on the restored copy (production untouched)")
            else:
                report.migration_rehearsal = "ok"
    finally:
        report.cleanup_ok = target.destroy()
        if not report.cleanup_ok:
            report.problems.append("the disposable restore database could not be destroyed; remove it by hand")
    return report


def record_rehearsal(layout: Layout, clock, report: RestoreReport, candidate: str) -> None:
    """Append the outcome of a standalone ``restore-check`` (evidence only:
    deployments restore-check their own fresh backup)."""
    entry = {
        "ts": rc.utc_iso(clock()),
        **report.evidence(),
        "ok": report.ok and candidate in ("ok", "skipped"),
        "candidate": candidate,
        "tool_version": rc.TOOL_VERSION,
    }
    fd = os.open(layout.rehearsal_log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(entry, sort_keys=True) + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
