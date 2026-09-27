#!/usr/bin/env python3
"""End-to-end rehearsal of D10.1 (#126): artesa-backup, local encrypted backups.

Everything is real except the machine boundary: a release artifact built from
git objects of this working tree, a disposable PostgreSQL 18 container with
the app's migrations and demo seed, the host's PostgreSQL 18 programs
(pg_dump, pg_restore, initdb, pg_ctl), the real ``age`` binary with two
throw-away key pairs (K1, K2) generated for this run, and a temp root. No
secret, key or provider of production is used; nothing leaves this machine.

    python tests/ops/rehearsal/run_backup_rehearsal.py --pg-bindir DIR [--age-bindir DIR] [--keep]

Exit status 0 only if every step behaves as designed (including the steps that
are supposed to fail).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[3]
REPO = HERE.parents[4]
OPS = BACKEND / "ops"
sys.path.insert(0, str(OPS))

import artesa_backup as ab  # noqa: E402
import build_release as br  # noqa: E402
import release_artifact as ra  # noqa: E402
import release_common as rc  # noqa: E402
import release_probe as rp  # noqa: E402

CONTAINER = "d10-backup-rehearsal-pg18"
PG_PORT = 55441
APP_USER, APP_PASSWORD = "backup_rehearsal_app", "Backup!Rehearsal#Pw26"
DB = "backup_rehearsal_prod_test"   # the app seed only writes to a test-marked database
URL = f"postgresql://{APP_USER}:{urllib.parse.quote(APP_PASSWORD, safe='')}@127.0.0.1:{PG_PORT}/{DB}"
STEPS: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    STEPS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)
    return bool(ok)


def sh(*argv: str, env=None, cwd=None, check=True, input_bytes=None) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), env=env, cwd=cwd, check=check, capture_output=True, input=input_bytes)


def psql(sql: str, db: str = "postgres") -> str:
    return sh("docker", "exec", "-i", CONTAINER, "psql", "-U", "postgres", "-d", db, "-Atqc", sql).stdout.decode().strip()


def start_postgres() -> None:
    sh("docker", "rm", "-f", CONTAINER, check=False)
    sh("docker", "run", "-d", "--rm", "--name", CONTAINER, "--tmpfs", "/var/lib/postgresql", "-e", "POSTGRES_PASSWORD=rootpw",
       "-p", f"127.0.0.1:{PG_PORT}:5432", "postgres:18")
    for _ in range(60):
        if sh("docker", "exec", CONTAINER, "pg_isready", "-U", "postgres", check=False).returncode == 0:
            time.sleep(1)
            break
        time.sleep(1)
    psql(f"CREATE ROLE {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE")
    psql(f"CREATE DATABASE {DB} OWNER {APP_USER}")


def git(clone: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "rehearsal", "GIT_AUTHOR_EMAIL": "r@example.com", "GIT_COMMITTER_NAME": "rehearsal", "GIT_COMMITTER_EMAIL": "r@example.com"}
    return subprocess.run(["git", "-C", str(clone), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                          check=True, capture_output=True, text=True, env=env).stdout.strip()


def plaintext_files(base: Path) -> list[str]:
    found = []
    for path in base.rglob("*"):
        if path.is_file():
            head = path.read_bytes()[:512]
            if head.startswith(b"PGDMP") or b"ustar" in head[257:265]:
                found.append(str(path.relative_to(base)))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bindir", default="/usr/lib/postgresql/18/bin")
    parser.add_argument("--age-bindir", default=None, help="directory with age and age-keygen (default: PATH)")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    pg_bindir = Path(args.pg_bindir).resolve()
    extra = [str(pg_bindir)] + ([str(Path(args.age_bindir).resolve())] if args.age_bindir else [])
    os.environ["PATH"] = ":".join(extra + [os.environ["PATH"]])
    for tool in ("pg_dump", "pg_restore", "age", "age-keygen"):
        if shutil.which(tool) is None:
            print(f"missing program: {tool}", file=sys.stderr)
            return 2

    work = Path(tempfile.mkdtemp(prefix="artesa-backup-rehearsal-"))
    root = work / "root"
    keys = work / "offline-keys"   # stands for the operator's offline machine: never under root/
    try:
        print("\n== setup: PostgreSQL 18 container, release from git objects, throw-away age keys")
        start_postgres()
        step("PostgreSQL container is version 18", psql("SHOW server_version").startswith("18"))
        clone = work / "clone"
        sh("git", "clone", "-q", "--no-hardlinks", str(REPO), str(clone))
        shutil.copytree(BACKEND, clone / "backend", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".venv", "venv", "__pycache__", ".pytest_cache", ".env", "*.pyc"))
        if git(clone, "status", "--porcelain"):
            git(clone, "add", "-A", "backend"); git(clone, "commit", "-q", "-m", "rehearsal: working-tree overlay")
        built = br.build_release(clone, work / "incoming", ref="HEAD", rehearsal=True)
        verified = ra.verify_artifact(built.artifact)
        rid = verified.release_id
        for d in ("bin", "releases", "shared/backups", "shared/state"):
            (root / d).mkdir(parents=True, exist_ok=True)
        os.chmod(root / "shared", 0o700)
        release_dir = root / "releases" / rid
        ra.extract_artifact(verified, release_dir)
        # the dev venv stands for the release venv (artesa-backup only runs its python for the db probe)
        os.symlink(Path(sys.prefix), release_dir / "venv", target_is_directory=True)
        os.symlink(f"releases/{rid}", root / "current")
        env_path = root / "shared" / ".env"
        env_path.write_text(f"APP_ENV=production\nDATABASE_URL={URL}\nDEBUG=False\nCORS_ALLOWED_ORIGINS=https://artesanfc.com\n")
        os.chmod(env_path, 0o600)
        child = rp.child_env(rp.read_env_file(env_path).values)
        up = sh(str(release_dir / "venv" / "bin" / "python"), "-m", "alembic", "upgrade", "head", env=child, cwd=release_dir, check=False)
        seed = sh(str(release_dir / "venv" / "bin" / "python"), "-m", "app.db.seed", env={**child, "APP_ENV": "test"}, cwd=release_dir, check=False)
        rows = psql("SELECT (SELECT count(*) FROM artisan)||','||(SELECT count(*) FROM piece)", DB)
        step("fixture database: migrations + demo seed", up.returncode == 0 and seed.returncode == 0 and rows != "0,0", f"artisan,piece = {rows}")
        keys.mkdir(mode=0o700)
        pubs = []
        for name in ("K1", "K2"):
            sh("age-keygen", "-o", str(keys / name))
            pubs.append(sh("age-keygen", "-y", str(keys / name)).stdout.decode().strip())
        base = root / "shared" / "backup"
        base.mkdir(mode=0o700)
        (base / "backup.env").write_text(f"ARTESA_BACKUP_AGE_RECIPIENT_K1={pubs[0]}\nARTESA_BACKUP_AGE_RECIPIENT_K2={pubs[1]}\n")
        os.chmod(base / "backup.env", 0o600)
        step("two throw-away recipients configured; no private key under the root",
             all(p.startswith("age1") for p in pubs) and "AGE-SECRET-KEY" not in "".join(p.read_text(errors="ignore") for p in (root / "shared").rglob("*") if p.is_file()))

        from datetime import datetime, timezone
        clock_base = [datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)]

        def cli(*argv: str, age: str = "age") -> tuple[int, str]:
            out: list[str] = []
            ctx = ab.Context(root=root, rehearsal=True, out=out.append, pg_bindir=str(pg_bindir), age=age, clock=lambda: clock_base[0],
                             deploy_lock_wait=0.0)
            code = ab.main(["--root", str(root), "--rehearsal", *argv], ctx)
            return code, "\n".join(out)

        print("\n== B1 run: dump -> restore-check (real initdb cluster) -> age K1+K2 -> plaintext unlinked")
        code, out = cli("run", "--scheduled")
        state = json.loads((base / "state" / "status.json").read_text())
        [bdir] = [p for p in (base / "encrypted").iterdir()]
        step("run exit 0, one encrypted backup", code == 0 and bdir.name == state["last_backup_id"], out.splitlines()[-2] if out else "")
        step("restore-check on a disposable PostgreSQL 18 passed and was destroyed",
             state["last_restore_check"]["ok"] and state["last_restore_check"]["target_destroyed"] and state["last_restore_check"]["counts_match"],
             f"{state['last_restore_check']['tables']} tables")
        step("no plaintext anywhere under shared/backup (dump/tar), staging empty", plaintext_files(base) == [] and list((base / "staging").iterdir()) == [])
        bundle = bdir / "bundle.tar.age"
        head = bundle.read_bytes()[:400]
        step("age v1 file with exactly two X25519 stanzas", head.startswith(b"age-encryption.org/v1\n") and head.count(b"-> X25519 ") == 2)

        print("\n== B2 recovery path off the host: decrypt with K1 and with K2, extract, restore-test the plaintext")
        restored = {}
        for name in ("K1", "K2"):
            plain = work / f"decrypted-{name}.tar"
            dec = sh("age", "--decrypt", "-i", str(keys / name), "-o", str(plain), str(bundle), check=False)
            restored[name] = dec.returncode == 0 and plain.stat().st_size > 0
        step("the bundle decrypts with K1 and, independently, with K2", all(restored.values()), str(restored))
        extract = work / "extracted"
        with tarfile.open(work / "decrypted-K2.tar") as tar:
            names = tar.getnames()
            tar.extractall(extract, filter="data")
        manifest = json.loads((extract / "manifest.json").read_text())
        step("bundle holds dump + manifest + recovery metadata; manifest checksum matches the dump",
             {"database.dump", "manifest.json", "recovery/RELEASE.json"} <= set(names)
             and manifest["files"]["database.dump"]["sha256"] == rc.sha256_file(str(extract / "database.dump"))
             and manifest["application"]["release_id"] == rid and manifest["encryption"]["recipients"] == pubs)
        step("manifest carries no credential", APP_PASSWORD not in json.dumps(manifest) and URL not in json.dumps(manifest))
        code, out = cli("restore-test", str(extract))
        step("restore-test of the decrypted bundle (drill path) PASS", code == 0 and "restore-test PASS" in out, out.splitlines()[-1] if out else "")
        shutil.rmtree(extract); [p.unlink() for p in work.glob("decrypted-*.tar")]

        print("\n== B3 status and verify (no private key)")
        code, out = cli("status")
        step("status: success, OFFSITE NOT CONFIGURED, D10 INCOMPLETE, not stale", code == 0 and "OFFSITE: NOT CONFIGURED" in out and "D10: INCOMPLETE" in out and "STALE" not in out)
        code, out = cli("verify", "--all")
        step("verify --all PASS without a private key", code == 0 and "[PASS]" in out)

        print("\n== B4 retention: 8 more daily runs keep the newest 7")
        for day in range(1, 9):
            clock_base[0] = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc) + timedelta(days=day)
            assert cli("run")[0] == 0
        kept = sorted(p.name for p in (base / "encrypted").iterdir())
        step("7 encrypted backups kept, the oldest two removed", len(kept) == 7 and kept[0].startswith("20261003T"), f"{kept[0]} .. {kept[-1]}")
        n08 = root / "shared" / "backups"
        step("shared/backups (deploy tool) untouched", list(n08.iterdir()) == [])

        print("\n== B5 refusals: missing K2, corrupt dump, lock conflict -- nothing kept, no plaintext")
        clock_base[0] += timedelta(hours=1)
        cfg = (base / "backup.env").read_text()
        (base / "backup.env").write_text(cfg.splitlines()[0] + "\n"); os.chmod(base / "backup.env", 0o600)
        before = sorted(p.name for p in (base / "encrypted").iterdir())
        code, out = cli("run")
        step("missing K2: exit 20, no new backup, no plaintext", code == 20 and sorted(p.name for p in (base / "encrypted").iterdir()) == before
             and plaintext_files(base) == [] and "K2 is missing" in out.replace("ARTESA_BACKUP_AGE_RECIPIENT_", ""))
        (base / "backup.env").write_text(cfg); os.chmod(base / "backup.env", 0o600)
        fakebin = work / "corrupt-bin"
        fakebin.mkdir()
        (fakebin / "pg_dump").write_text("#!/bin/sh\nfor a in \"$@\"; do case \"$a\" in --version) echo 'pg_dump (PostgreSQL) 18.6'; exit 0;; --file=*) f=\"${a#--file=}\";; esac; done\n"
                                         "printf 'PGDMP-this-is-not-a-real-archive' > \"$f\"\n")
        os.chmod(fakebin / "pg_dump", 0o755)
        saved = os.environ["PATH"]
        os.environ["PATH"] = f"{fakebin}:{saved}"
        clock_base[0] += timedelta(minutes=5)
        code, out = cli("run")
        os.environ["PATH"] = saved
        step("corrupt dump: exit 31, refused before encryption, no plaintext", code == 31 and plaintext_files(base) == []
             and sorted(p.name for p in (base / "encrypted").iterdir()) == before, out.splitlines()[-2] if out else "")
        fd = os.open(base / "backup.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        code, out = cli("run")
        os.close(fd)
        step("backup lock held: exit 11, second run refused", code == 11 and "holds the backup lock" in out)
        state = json.loads((base / "state" / "status.json").read_text())
        step("state counts the consecutive failures", state["consecutive_failures"] == 2 and state["last_result"] == "failure")
        clock_base[0] += timedelta(minutes=5)
        step("a later run succeeds and resets the failure count", cli("run")[0] == 0 and json.loads((base / "state" / "status.json").read_text())["consecutive_failures"] == 0)

        print("\n== B6 secrets and cleanup")
        blob = b"".join(p.read_bytes() for p in base.rglob("*") if p.is_file())
        step("no database password or URL anywhere under shared/backup", APP_PASSWORD.encode() not in blob and URL.encode() not in blob)
        leftovers = [p for p in Path(tempfile.gettempdir()).glob("artesa-rc-*") if p.stat().st_mtime > time.time() - 3600 and any(p.iterdir())]
        step("no disposable restore cluster left behind", not (base / "staging").exists() or list((base / "staging").iterdir()) == [], f"{len(leftovers)} socket dirs")
    finally:
        if not args.keep:
            sh("docker", "rm", "-f", CONTAINER, check=False)
            for sub, dirs, _files in os.walk(work):
                for d in dirs:
                    try:
                        os.chmod(os.path.join(sub, d), 0o700)
                    except OSError:
                        pass
            shutil.rmtree(work, ignore_errors=True)

    failed = [s for s in STEPS if not s[1]]
    print(f"\nBACKUP REHEARSAL: {len(STEPS) - len(failed)}/{len(STEPS)} steps behaved as designed" + ("" if not failed else f"; FAILED: {[s[0] for s in failed]}"))
    if not args.keep:
        print(f"cleanup: temp root removed: {not work.exists()}")
    else:
        print(f"kept for inspection: {work} (container {CONTAINER} still running)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
