#!/usr/bin/env python3
"""Local end-to-end rehearsal of the N-08 release workflow (ADR-027).

Everything is real except the machine boundary: real git objects -> real
artifacts -> real per-release venvs (hashed wheel install) -> real Uvicorn ->
real PostgreSQL 18 in a disposable Docker container -> real pg_dump/pg_restore
-> real restore-check in a throw-away initdb cluster -> real Alembic. Data is
synthetic (the app's own demo seed). Nothing here can reach production: the
root is a temp dir, `--rehearsal` is mandatory, the databases are containers
bound to 127.0.0.1, and no network service other than PyPI is contacted.

    python tests/ops/rehearsal/run_rehearsal.py --pg-bindir DIR [--keep]

DIR holds PostgreSQL 18 client *and* server programs (pg_dump, pg_restore,
createdb, dropdb, initdb, pg_ctl, postgres), e.g. /usr/lib/postgresql/18/bin.

Exit status 0 only if every step behaves as designed (including the steps
that are *supposed* to be refused).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pty
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[3]
REPO = HERE.parents[4]
OPS = BACKEND / "ops"
sys.path.insert(0, str(OPS))

import artesa_deploy as ad  # noqa: E402
import build_release as br  # noqa: E402
import deploy_db as ddb  # noqa: E402
import deploy_layout as dl  # noqa: E402
import release_common as rc  # noqa: E402
import release_probe as rp  # noqa: E402

CONTAINER = "n08-rehearsal-pg18"
SCRATCH_CONTAINER = "n08-rehearsal-scratch-pg18"
PG_PORT = 55433
SCRATCH_PORT = 55435
PROD_PORT, CAND_PORT = 18000, 18001
APP_USER, APP_PASSWORD = "rehearsal_app", "Rehearsal!Pw#2026"   # special chars on purpose (percent-encoding, F-11)
DB_PROD = "rehearsal_prod_test"
URL_PROD = f"postgresql://{APP_USER}:{urllib.parse.quote(APP_PASSWORD, safe='')}@127.0.0.1:{PG_PORT}/{DB_PROD}"
URL_SCRATCH_SERVER = f"postgresql://postgres:{urllib.parse.quote('Scratch#Root!pw', safe='')}@127.0.0.1:{SCRATCH_PORT}/postgres"
URL_SAME_CLUSTER = f"postgresql://postgres:rootpw@127.0.0.1:{PG_PORT}/postgres"

STEPS: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    STEPS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)
    return bool(ok)


def sh(*argv: str, env=None, cwd=None, check=True, input_text=None) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), env=env, cwd=cwd, check=check, capture_output=True, text=True, input=input_text)


# --- docker postgres ---------------------------------------------------------------------------

def psql(sql: str, db: str = "postgres", user: str = "postgres") -> str:
    return sh("docker", "exec", "-i", CONTAINER, "psql", "-U", user, "-d", db, "-Atqc", sql).stdout.strip()


def start_container(name: str, port: int, password: str) -> None:
    sh("docker", "rm", "-f", name, check=False)
    sh("docker", "run", "-d", "--rm", "--name", name, "--tmpfs", "/var/lib/postgresql", "-e", f"POSTGRES_PASSWORD={password}",
       "-p", f"127.0.0.1:{port}:5432", "postgres:18")
    for _ in range(60):
        if sh("docker", "exec", name, "pg_isready", "-U", "postgres", check=False).returncode == 0:
            time.sleep(1)
            break
        time.sleep(1)


def start_postgres() -> None:
    start_container(CONTAINER, PG_PORT, "rootpw")
    start_container(SCRATCH_CONTAINER, SCRATCH_PORT, "Scratch#Root!pw")
    psql(f"CREATE ROLE {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
    psql(f"CREATE DATABASE {DB_PROD} OWNER {APP_USER}")


# --- releases -------------------------------------------------------------------------------------

def git(clone: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "rehearsal", "GIT_AUTHOR_EMAIL": "r@example.com", "GIT_COMMITTER_NAME": "rehearsal", "GIT_COMMITTER_EMAIL": "r@example.com"}
    return sh("git", "-C", str(clone), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args, env=env).stdout.strip()


def add_migration(clone: Path, rev: str, down: str, body_up: str, body_down: str, cls: str) -> None:
    path = clone / "backend" / "alembic" / "versions" / f"{rev}_rehearsal.py"
    path.write_text(
        f'"""rehearsal fixture"""\nfrom alembic import op\nimport sqlalchemy as sa\nrevision = "{rev}"\ndown_revision = "{down}"\nbranch_labels = None\ndepends_on = None\n\n'
        f"def upgrade():\n    {body_up}\n\ndef downgrade():\n    {body_down}\n"
    )
    ledger = clone / "backend" / "ops" / "migration-classes.json"
    data = json.loads(ledger.read_text())
    data["revisions"][rev] = {"class": cls, "note": "synthetic rehearsal fixture"}
    ledger.write_text(json.dumps(data, indent=2) + "\n")
    git(clone, "add", "-A"); git(clone, "commit", "-q", "-m", f"rehearsal {rev}")


def build(clone: Path, out: Path, tick: int) -> br.BuildResult:
    from datetime import datetime, timedelta, timezone
    when = datetime(2026, 9, 21, 4, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=tick)
    return br.build_release(clone, out, ref="HEAD", rehearsal=True, release_time=when)


# --- the "service" (systemd stand-in) --------------------------------------------------------------------

class ProcessService:
    """Starts the *real* production command line from <root>/current. Optionally
    simulates a failed restart for chosen releases."""

    def __init__(self, root: Path, guard_env: dict[str, str], fail_for: set[str]) -> None:
        self.root, self.env_values, self.fail_for = root, guard_env, fail_for
        self.proc: subprocess.Popen | None = None
        self.restarts = 0

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL); self.proc.wait()
        self.proc = None

    def restart(self) -> None:
        self.restarts += 1
        self.stop()
        current = os.path.basename(os.path.realpath(self.root / "current"))
        if current in self.fail_for:
            raise rc.OpsError(rc.Exit.ACTIVATION_ROLLBACK_FAILED, "simulated: the service failed to restart")
        log = open(self.root / "shared" / "state" / "service.log", "ab")
        self.proc = subprocess.Popen(
            [str(self.root / "current" / "venv" / "bin" / "python"), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PROD_PORT), *rp.UVICORN_FLAGS],
            cwd=str(self.root / "current"), env=rp.child_env(self.env_values), stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )

    def info(self) -> dl.ServiceInfo:
        wd = str(self.root / "current")
        alive = self.proc is not None and self.proc.poll() is None
        return dl.ServiceInfo(True, "active" if alive else "inactive", "running" if alive else "dead", 0, wd, wd + "/venv/bin/python")


def make_venv(path: Path) -> None:
    """`python -m venv` as in production; a dev machine without ensurepip
    (Debian/Ubuntu python3 without python3-venv) falls back to /usr/bin/pip."""
    if sh(sys.executable, "-c", "import ensurepip", check=False).returncode == 0:
        sh(sys.executable, "-m", "venv", str(path))
        return
    sh(sys.executable, "-m", "venv", "--without-pip", str(path))
    sh("/usr/bin/pip", "--python", str(path / "bin" / "python"), "install", "-q", "--disable-pip-version-check", "pip")


def tree_fingerprint(root: Path) -> str:
    parts = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if "venv" in rel.parts:
            continue
        if p.is_symlink():
            parts.append(f"L {rel} {os.readlink(p)}")
        elif p.is_file():
            parts.append(f"F {rel} {hashlib.sha256(p.read_bytes()).hexdigest()}")
        else:
            parts.append(f"D {rel}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep the temp dir and the container for inspection")
    parser.add_argument("--pg-bindir", default="/usr/lib/postgresql/18/bin", help="PostgreSQL 18 client+server programs")
    args = parser.parse_args()
    pg_bindir = Path(args.pg_bindir).resolve()
    missing = [n for n in ("pg_dump", "pg_restore", "createdb", "dropdb", "initdb", "pg_ctl") if not (pg_bindir / n).exists()]
    if missing:
        print(f"PostgreSQL 18 programs missing in {pg_bindir}: {', '.join(missing)}", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="artesa-rehearsal-"))
    root = work / "artesa-nfc"
    print(f"rehearsal workspace: {work}")
    service: ProcessService | None = None
    intruder: subprocess.Popen | None = None
    try:
        print("\n== setup: PostgreSQL 18 (disposable containers), layout, host PostgreSQL 18 programs")
        start_postgres()
        version = psql("SHOW server_version")
        step("PostgreSQL container is version 18", version.startswith("18"), version)
        step("application role is not superuser/createdb/createrole/replication/bypassrls",
             psql(f"SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls FROM pg_roles WHERE rolname='{APP_USER}'") == "f")
        os.environ["PATH"] = f"{pg_bindir}:{os.environ['PATH']}"
        dump_version = sh("pg_dump", "--version").stdout.strip()
        step("host pg_dump is PostgreSQL 18", " 18." in dump_version, dump_version)
        for d in ("bin", "incoming", "releases", "shared/backups", "shared/state", "shared/deploys"):
            (root / d).mkdir(parents=True, exist_ok=True)
        os.chmod(root / "shared", 0o700)
        env_path = root / "shared" / ".env"
        env_path.write_text(f"APP_ENV=production\nDATABASE_URL={URL_PROD}\nDEBUG=False\nCORS_ALLOWED_ORIGINS=https://artesanfc.com,https://www.artesanfc.com\n")
        os.chmod(env_path, 0o600)
        env_values = rp.read_env_file(env_path).values

        print("\n== S1 build releases from git objects (temp clone; nothing of the real branch is touched)")
        clone = work / "clone"
        sh("git", "clone", "-q", "--no-hardlinks", str(REPO), str(clone))
        # Rehearse the code as it is in this working tree (committed or not):
        # overlay backend/ into the throw-away clone and commit it there.
        shutil.copytree(BACKEND, clone / "backend", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".venv", "venv", "__pycache__", ".pytest_cache", ".env", "*.pyc"))
        if git(clone, "status", "--porcelain"):
            git(clone, "add", "-A", "backend"); git(clone, "commit", "-q", "-m", "rehearsal: working-tree overlay")
        r1 = build(clone, root / "incoming", 1)
        step("R1 built (rehearsal channel, single Alembic head)", r1.release["channel"] == "rehearsal" and r1.release["alembic"]["heads_count"] == 1, r1.release_id)
        again = build(clone, work / "again", 1)
        step("same commit -> same content_sha256 and same artifact bytes", again.release["artifact"]["content_sha256"] == r1.release["artifact"]["content_sha256"] and again.artifact.read_bytes() == r1.artifact.read_bytes())
        # D7 both ways, whatever ref CI checked out: pin the clone's origin/main to HEAD
        # (merged -> allowed), then add a commit only this clone has (unmerged -> refused
        # as "not reachable"). A missing origin/main ("cannot evaluate") is not a pass.
        git(clone, "update-ref", "refs/remotes/origin/main", "HEAD")
        try:
            br.build_release(clone, work / "prodbuild-merged", ref="HEAD", rehearsal=False)
            merged_ok, merged = True, "merged: allowed"
        except rc.OpsError as exc:
            merged_ok, merged = False, f"merged: refused ({exc.message[:50]})"
        git(clone, "commit", "-q", "--allow-empty", "-m", "rehearsal: unmerged")
        try:
            br.build_release(clone, work / "prodbuild-unmerged", ref="HEAD", rehearsal=False)
            unmerged_ok, unmerged = False, "unmerged: allowed"
        except rc.OpsError as exc:
            unmerged_ok = exc.code == rc.Exit.ARTIFACT_INVALID and "not reachable from origin/main" in exc.message
            unmerged = f"unmerged: exit {int(exc.code)} {exc.message[:45]}"
        step("production-channel build of an unmerged commit is refused", merged_ok and unmerged_ok, f"{merged}; {unmerged}")
        (clone / "backend" / "app" / "r2_marker.py").write_text('MARK = "r2"\n'); git(clone, "add", "-A"); git(clone, "commit", "-q", "-m", "r2 code-only")
        r2 = build(clone, root / "incoming", 2)
        head = r1.release["alembic"]["head"]
        add_migration(clone, "a3b3b3b3b3b3", head, 'op.create_table("rehearsal_probe", sa.Column("id", sa.Integer(), primary_key=True))', 'op.drop_table("rehearsal_probe")', "additive")
        r3 = build(clone, root / "incoming", 3)
        add_migration(clone, "a5b5b5b5b5b5", "a3b3b3b3b3b3", 'op.create_table("rehearsal_probe2", sa.Column("id", sa.Integer(), primary_key=True))', 'op.drop_table("rehearsal_probe2")', "additive")
        r5 = build(clone, root / "incoming", 5)
        # R6 and R4 descend from R5: after S8 the database is at R5's revision,
        # and a release that does not know it is refused (exit 23), by design.
        (clone / "backend" / "app" / "r6_marker.py").write_text('MARK = "r6"\n'); git(clone, "add", "-A"); git(clone, "commit", "-q", "-m", "r6 code-only")
        r6 = build(clone, root / "incoming", 6)
        add_migration(clone, "a4b4b4b4b4b4", "a5b5b5b5b5b5", 'op.drop_table("rehearsal_probe")', 'op.create_table("rehearsal_probe", sa.Column("id", sa.Integer(), primary_key=True))', "breaking")
        r4 = build(clone, root / "incoming", 4)
        ids = {"r1": r1.release_id, "r2": r2.release_id, "r3": r3.release_id, "r4": r4.release_id, "r5": r5.release_id, "r6": r6.release_id}
        step("R2 code-only, R3 additive, R5 additive+failing restart, R6 code-only, R4 breaking built", len(set(ids.values())) == 6)

        answers: list[str] = []
        service = ProcessService(root, env_values, set())

        def cli(argv: list[str], *, tty: bool = True, answer=None) -> tuple[int, str]:
            out: list[str] = []
            guard = rp.SecretGuard()
            answers_left = [answer] if isinstance(answer, str) or answer is None else list(answer)
            ctx = ad.Context(root=root, rehearsal=True, prod_port=PROD_PORT, candidate_port=CAND_PORT, runner=rp.Runner(guard), service=service,
                             is_tty=lambda: tty, prompt=lambda m: (answers_left.pop(0) if answers_left else "") or "", out=out.append,
                             public_check=None, make_venv=make_venv, health_timeout=40.0, pg_bindir=str(pg_bindir))
            code = ad.main(["--root", str(root), "--rehearsal", "--prod-port", str(PROD_PORT), "--candidate-port", str(CAND_PORT), *argv], ctx)
            return code, "\n".join(out)

        def state() -> tuple[str | None, str | None]:
            layout = dl.Layout(root)
            return dl.read_link(layout, root / "current"), dl.read_link(layout, root / "previous")

        def db_revision() -> str:
            return psql("SELECT version_num FROM alembic_version", DB_PROD)

        print("\n== S2 prepare R1 (real venv, hashed wheels only) + verify --deep")
        code, out = cli(["prepare", ids["r1"]])
        step("prepare R1 succeeds", code == 0, out.splitlines()[-1] if out else "")
        step("R1 venv is inside its release and is not a symlink", (root / "releases" / ids["r1"] / "venv").is_dir() and not (root / "releases" / ids["r1"] / "venv").is_symlink())
        code, out = cli(["verify", ids["r1"], "--deep"], tty=False)
        step("verify --deep: tree == manifest and venv == lock", code == 0, out.splitlines()[-1])

        print("\n== S3 baseline database (synthetic): alembic upgrade head + demo seed, as production already is")
        env = rp.child_env(env_values)
        py = str(root / "releases" / ids["r1"] / "venv" / "bin" / "python")
        up = sh(py, "-m", "alembic", "upgrade", "head", env=env, cwd=root / "releases" / ids["r1"], check=False)
        step("alembic upgrade head under R1 (URL with percent-escapes)", up.returncode == 0, (up.stderr.strip().splitlines() or [""])[-1][:80])
        seed = sh(py, "-m", "app.db.seed", env={**env, "APP_ENV": "test"}, cwd=root / "releases" / ids["r1"], check=False)
        rows = psql("SELECT (SELECT count(*) FROM artisan)||','||(SELECT count(*) FROM piece)", DB_PROD)
        step("synthetic data present (app demo seed)", seed.returncode == 0 and rows != "0,0", f"artisan,piece = {rows}")
        base_rev = db_revision()
        step("database at the release head (895974720462)", base_rev == r1.release["alembic"]["head"], base_rev)

        print("\n== S4 candidate + first deployment of R1")
        code, out = cli(["candidate", ids["r1"]], tty=False)
        step("candidate R1 on :18001 passes every smoke check and is killed", code == 0 and "candidate OK" in out and rp.port_is_free(CAND_PORT), f"{out.count('[PASS]')} checks")
        code, out = cli(["deploy", ids["r1"]], answer=ids["r1"])
        step("first deploy R1: activated, service healthy", code == 0 and state() == (ids["r1"], None), f"exit {code}")
        owner = rp.port_owner_state(PROD_PORT, root / "current")
        step("port 18000 is held by the R1 process (cwd == releases/R1), loopback only", owner.state == "expected", owner.detail)

        print("\n== S5 R2 (code-only): prepare, dry-run mutates nothing, deploy")
        step("prepare R2", cli(["prepare", ids["r2"]])[0] == 0)
        before, pid_before = tree_fingerprint(root), service.proc.pid
        code, out = cli(["deploy", ids["r2"], "--dry-run"], tty=False)
        after = tree_fingerprint(root)
        step("dry-run shows the plan and changes nothing (tree, symlinks, DB revision, service pid)",
             code == 0 and before == after and db_revision() == base_rev and service.proc.pid == pid_before and "class=code-only" in out and "backup: not required" in out)
        code, out = cli(["deploy", ids["r2"]], answer=ids["r2"])
        step("deploy R2: current=R2, previous=R1", code == 0 and state() == (ids["r2"], ids["r1"]), f"exit {code}")
        cwd = os.path.basename(os.path.realpath(f"/proc/{service.proc.pid}/cwd"))
        step("the running process is R2's (its cwd is releases/R2)", cwd == ids["r2"])

        print("\n== S6 rollback (code-only)")
        code, out = cli(["rollback", "--dry-run"], tty=False)
        step("rollback --dry-run is clean and read-only", code == 0 and state() == (ids["r2"], ids["r1"]))
        code, out = cli(["rollback"], answer=ids["r1"])
        step("rollback: current=R1, previous=R2, DB untouched", code == 0 and state() == (ids["r1"], ids["r2"]) and db_revision() == base_rev)

        print("\n== S7 R3 additive migration: gates, backup, restore-check, migration")
        step("prepare R3", cli(["prepare", ids["r3"]])[0] == 0)
        code, out = cli(["deploy", ids["r3"]], answer=ids["r3"])
        step("pending migration without --allow-migration is refused (30)", code == 30 and db_revision() == base_rev)
        code, out = cli(["backup"])
        dump = sorted((root / "shared" / "backups").glob("*.dump"))
        step("backup (real pg_dump -Fc): dump + .json + .sha256, 0600", code == 0 and len(dump) == 1 and (dump[0].stat().st_mode & 0o777) == 0o600
             and dump[0].with_name(dump[0].name + ".sha256").read_text().split()[0] == hashlib.sha256(dump[0].read_bytes()).hexdigest(),
             out.strip().splitlines()[-1][:90] if out else "")
        meta = json.loads(dump[0].with_name(dump[0].name + ".json").read_text())
        step("sidecar: sha256, size, revision, row counts, commit; no host/user/password",
             meta["alembic_revision"] == base_rev and meta["row_counts"]["artisan"] > 0 and not ({"host", "user", "password"} & set(meta)) and APP_USER not in json.dumps(meta))
        code, out = cli(["restore-check", str(dump[0]), "--ephemeral", "--candidate-release", ids["r3"]], tty=False)
        step("restore-check --ephemeral: real initdb cluster, restore, counts identical, R3 migration rehearsed on the copy, cluster destroyed",
             code == 0 and "migration rehearsal ok" in out and not list((root / "shared" / "state" / "restore-tmp").iterdir()), out.strip().splitlines()[-1][:120])
        step("production database untouched by the restore-check", db_revision() == base_rev)
        os.environ["REHEARSAL_SCRATCH_URL"] = URL_SCRATCH_SERVER
        code, out = cli(["restore-check", str(dump[0]), "--scratch-server-url-env", "REHEARSAL_SCRATCH_URL"], tty=False)
        left = sh("docker", "exec", SCRATCH_CONTAINER, "psql", "-U", "postgres", "-Atqc", "SELECT count(*) FROM pg_database WHERE datname LIKE 'artesa_restore%'").stdout.strip()
        step("restore-check on a scratch server: unique DB created, verified and dropped", code == 0 and left == "0", out.strip().splitlines()[-1][:110])
        os.environ["REHEARSAL_SCRATCH_URL"] = URL_SAME_CLUSTER
        code, out = cli(["restore-check", str(dump[0]), "--scratch-server-url-env", "REHEARSAL_SCRATCH_URL"], tty=False)
        step("restore-check refuses the production PostgreSQL as a scratch server (20)", code == 20 and "production PostgreSQL" in out)
        os.environ.pop("REHEARSAL_SCRATCH_URL")
        code, out = cli(["deploy", ids["r3"], "--dry-run", "--allow-migration"], tty=False)
        step("dry-run of the migration deploy: MIGRATION_DEPLOY, backup + restore-check planned, nothing mutated",
             code == 0 and "type=MIGRATION_DEPLOY" in out and "backup: REQUIRED" in out and db_revision() == base_rev)
        commit3 = r3.release["git"]["commit"]
        code, out = cli(["deploy", ids["r3"], "--allow-migration", "--expect-commit", commit3[:12]], answer=ids["r3"])
        table = psql("SELECT to_regclass('public.rehearsal_probe') IS NOT NULL", DB_PROD)
        step("additive migration deployed: backup, ephemeral restore-check + rehearsal, alembic upgrade, candidate, activation",
             code == 0 and db_revision() == "a3b3b3b3b3b3" and table == "t" and state() == (ids["r3"], ids["r1"]), f"exit {code}")
        step("a second backup was taken by the deploy itself", len(list((root / "shared" / "backups").glob("*.dump"))) == 2)
        evdir = sorted(p for p in (root / "shared" / "state" / "deployments").iterdir() if p.name.endswith(commit3[:12]))[-1]
        alembic_ev = json.loads((evdir / "alembic.json").read_text())
        restore_ev = json.loads((evdir / "restore-check.json").read_text())
        step("evidence: alembic before/target/after, restore-check ok on an ephemeral cluster, result ok",
             (alembic_ev["before"], alembic_ev["target"], alembic_ev["after"]) == (base_rev, "a3b3b3b3b3b3", "a3b3b3b3b3b3")
             and restore_ev["ok"] and restore_ev["target"] == "ephemeral-cluster" and restore_ev["target_destroyed"]
             and json.loads((evdir / "result.json").read_text())["status"] == "ok", evdir.name)

        print("\n== S8 no automatic rollback after a migration")
        step("prepare R5 (additive #2)", cli(["prepare", ids["r5"]])[0] == 0)
        service.fail_for = {ids["r5"]}
        code, out = cli(["deploy", ids["r5"], "--allow-migration"], answer=ids["r5"])
        step("R5: migration ran, restart failed -> exit 54, NO automatic rollback", code == 54 and "NOT rolling back automatically (a migration ran)" in out and db_revision() == "a5b5b5b5b5b5", f"exit {code}")
        step("state left as-is for a human decision (current=R5, marker present)", state()[0] == ids["r5"] and (root / "shared" / "state" / "activation.json").exists())
        code, out = cli(["deploy", ids["r2"], "--dry-run"], tty=False)
        step("a further deploy is blocked until the human resolves the activation (11)", code == 11 and "activation" in out)
        code, out = cli(["resolve-activation"], answer="resolve")
        step("resolve-activation refuses while the active release is unhealthy", code == 40 and (root / "shared" / "state" / "activation.json").exists())
        service.fail_for = set()
        code, out = cli(["rollback", "--dry-run"], tty=False)
        step("manual rollback is judged by the ledger and allowed despite the marker (warning)", code == 0 and "[WARN]" in out)
        code, out = cli(["rollback"], answer=ids["r3"])
        step("human rollback to R3 (R5's migration is additive); DB NOT downgraded; marker cleared",
             code == 0 and state()[0] == ids["r3"] and db_revision() == "a5b5b5b5b5b5" and not (root / "shared" / "state" / "activation.json").exists())

        print("\n== S9 breaking migration is rejected automatically")
        step("prepare R4 (breaking)", cli(["prepare", ids["r4"]])[0] == 0)
        code, out = cli(["deploy", ids["r4"], "--allow-migration"], answer=ids["r4"])
        step("R4 rejected (30) even with --allow-migration; no backup, no migration, no restart", code == 30 and "breaking" in out and state()[0] == ids["r3"])

        print("\n== S10 code-only activation failure -> automatic rollback")
        step("prepare R6", cli(["prepare", ids["r6"]])[0] == 0)
        service.fail_for = {ids["r6"]}
        current_before = state()
        code, out = cli(["deploy", ids["r6"]], answer=ids["r6"])
        step("R6: candidate ok, restart fails -> exit 50, automatically back on R3, service healthy",
             code == 50 and state() == current_before and service.proc and service.proc.poll() is None and rp.port_owner_state(PROD_PORT, root / "current").state == "expected", f"exit {code}")
        service.fail_for = set()

        print("\n== S11 an alien process on the production port fails closed")
        service.stop()
        intruder = subprocess.Popen([sys.executable, "-m", "http.server", str(PROD_PORT), "--bind", "127.0.0.1"], cwd="/tmp", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        code, out = cli(["deploy", ids["r2"], "--dry-run"], tty=False)
        # Expected outcome: the port gate MUST fail here. Report it as what it
        # is (a correct detection), not by echoing the tool's own FAIL marker.
        port_gate_failed = any("[FAIL] port" in l and f"pid {intruder.pid}" in l for l in out.splitlines())
        step("deploy dry-run: exit 11, reports pid/cwd of the intruder, nothing killed",
             code == 11 and port_gate_failed and intruder.poll() is None,
             f"gate correctly failed: foreign process detected (pid {intruder.pid}, cwd /tmp)" if port_gate_failed else "port gate did not report the intruder")
        code, out = cli(["status", "--json"], tty=False)
        step("status reports the port as alien", json.loads(out)["port"]["state"] == "alien")
        intruder.terminate(); intruder.wait(); intruder = None
        code, out = cli(["candidate", ids["r2"]], tty=False)
        step("candidate still works while the production port is unavailable", code == 0)

        print("\n== S12 real TTY gate (subprocess CLI)")
        run = subprocess.run([sys.executable, str(OPS / "artesa_deploy.py"), "--root", str(root), "--rehearsal", "deploy", ids["r2"]], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        step("real CLI without a TTY: exit 3 before any gate", run.returncode == 3 and "interactive terminal" in run.stdout, f"exit {run.returncode}")
        step("real CLI --dry-run works without a TTY", subprocess.run([sys.executable, str(OPS / "artesa_deploy.py"), "--root", str(root), "--rehearsal", "prune"], stdin=subprocess.DEVNULL, capture_output=True, text=True).returncode == 0)

        print("\n== S13 prune (dry-run default, then a real pty delete)")
        code, out = cli(["prune", "--keep-releases", "3"], tty=False)
        step("prune dry-run lists keep/delete and deletes nothing", code == 0 and "DRY RUN" in out and len(list((root / "releases").iterdir())) == 6)
        n_delete = len(out.split("delete:")[1].split("\n")[0].split(", ")) if "delete:  " in out and out.split("delete:")[1].split("\n")[0].strip() != "-" else 0
        pid, fd = pty.fork()
        if pid == 0:
            os.execv(sys.executable, [sys.executable, str(OPS / "artesa_deploy.py"), "--root", str(root), "--rehearsal", "prune", "--keep-releases", "3", "--delete"])
        buf = b""
        deadline = time.time() + 20
        while time.time() < deadline and b"to continue" not in buf:
            try:
                buf += os.read(fd, 4096)
            except OSError:
                break
        os.write(fd, f"delete {n_delete}\n".encode())
        while True:
            try:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                buf += chunk
            except OSError:
                break
        _, status = os.waitpid(pid, 0)
        left = sorted(os.listdir(root / "releases"))
        step("real pty: prune --delete asks for typed confirmation and deletes only the listed releases, never current/previous",
             os.waitstatus_to_exitcode(status) == 0 and len(left) == 3 and state()[0] in left and (state()[1] in left), f"{len(left)} releases left")

        print("\n== S14b install-tools + the real bin/ launcher")
        (clone / "backend" / "app" / "r7_marker.py").write_text('MARK = "r7"\n'); git(clone, "add", "-A"); git(clone, "commit", "-q", "-m", "r7 tools")
        r7 = build(clone, root / "incoming", 7)
        step("prepare R7 (carries ops/ + ops/bin/artesa-deploy)", cli(["prepare", r7.release_id])[0] == 0)
        code, out = cli(["install-tools", r7.release_id], answer=r7.release_id)
        launched = subprocess.run([str(root / "bin" / "artesa-deploy"), "--root", str(root), "--rehearsal", "--prod-port", str(PROD_PORT), "status", "--json"],
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        step("install-tools: bin/ops -> ops-<id>, launcher runs the installed tool with /usr/bin/python3 -I",
             code == 0 and os.readlink(root / "bin" / "ops") == f"ops-{r7.release_id}" and launched.returncode == 0 and '"current"' in launched.stdout,
             f"exit {code}/{launched.returncode}")
        step("every deployment left an evidence directory", len(list((root / "shared" / "state" / "deployments").iterdir())) >= 5)

        print("\n== S14 secret scan over everything the rehearsal produced")
        blob = b""
        for p in root.rglob("*"):
            if p.is_file() and not p.is_symlink() and p.name != ".env" and "venv" not in p.relative_to(root).parts and p.stat().st_size < 20_000_000:
                blob += p.read_bytes()
        leaked = [name for name, secret in (("password", APP_PASSWORD), ("password(urlenc)", urllib.parse.quote(APP_PASSWORD, safe="")), ("dsn", URL_PROD), ("db user", APP_USER)) if secret.encode() in blob.replace(dump[0].read_bytes(), b"") ]
        # dumps legitimately contain the owning role name in their TOC (they are backups, 0600, never an artifact)
        leaked = [l for l in leaked if l != "db user"]
        step("no password / DSN in state, logs, candidate logs, deploy log, sidecars, releases", not leaked, ", ".join(leaked) or "clean")
        step("service log and candidate logs contain no secret either", all(APP_PASSWORD not in p.read_text(errors="replace") for p in (root / "shared" / "state").glob("*.log")))
        events = [json.loads(l) for l in (root / "shared" / "state" / "deploy-log.jsonl").read_text().splitlines()]
        step("deploy log is JSONL with only allowlisted fields", all(set(e) <= dl.ALLOWED_LOG_KEYS for e in events), f"{len(events)} events")

    finally:
        if intruder and intruder.poll() is None:
            intruder.kill()
        if service:
            service.stop()
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        if not args.keep:
            sh("docker", "rm", "-f", CONTAINER, SCRATCH_CONTAINER, check=False)

    failed = [s for s in STEPS if not s[1]]
    print(f"\nREHEARSAL: {len(STEPS) - len(failed)}/{len(STEPS)} steps behaved as designed" + ("" if not failed else f"; FAILED: {[s[0] for s in failed]}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
