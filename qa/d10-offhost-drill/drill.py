#!/usr/bin/env python3
"""Off-host recovery drill for one encrypted ArtesaNFC backup (D10, #126).

Runs on the operator's trusted machine, NEVER on easerver: it needs a private
age identity (K1 or K2), and private keys never reach the server
(docs/BACKUP.md §7). It proves that one backup made by ``artesa-backup run``
can actually be recovered:

  1. the copied ``bundle.tar.age`` matches its public ``meta.json`` (size, sha256)
     and has an age v1 header with the expected number of X25519 recipients;
  2. it decrypts with the identity given (``age -d -i``);
  3. the bundle holds ``database.dump`` + ``manifest.json``, the dump matches the
     manifest's sha256 and the manifest's backup_id matches meta.json;
  4. the dump restores (``pg_restore --no-owner --no-privileges``) into a
     throw-away PostgreSQL container with no network, and the Alembic revision
     and every table's row count equal the manifest's.

Everything it creates is removed on exit, also on failure or Ctrl-C: the
decrypted plaintext (a private 0700 temp dir; on an SSD unlink is not a secure
erase, same caveat as the server) and the container. Nothing is written next to
the backup. The identity file is only passed to ``age``; it is never read,
copied or printed by this script.

    python3 qa/d10-offhost-drill/drill.py <backup_dir> <identity_file> [--recipients 2]

<backup_dir> holds ``bundle.tar.age`` and ``meta.json`` copied from
``<root>/shared/backup/encrypted/<backup_id>/``.

Exit: 0 = the backup was recovered and matches, 1 = a check failed,
2 = setup or prerequisite problem.

Environment (optional): AGE (age binary, default ``age``), DOCKER (default
``docker``), POSTGRES_IMAGE (default ``postgres:18``, must match the
manifest's server major or be newer).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

AGE_MAGIC = b"age-encryption.org/v1\n"
BUNDLE_NAME = "bundle.tar.age"
META_NAME = "meta.json"
EXIT_OK, EXIT_FAIL, EXIT_SETUP = 0, 1, 2


class DrillError(Exception):
    def __init__(self, message: str, code: int = EXIT_FAIL):
        super().__init__(message)
        self.code = code


def say(message: str) -> None:
    print(message, flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def age_recipient_stanzas(path: Path) -> int:
    """X25519 stanzas in the age v1 header, read without any key."""
    with open(path, "rb") as handle:
        head = handle.read(64 * 1024)
    if not head.startswith(AGE_MAGIC):
        raise DrillError(f"{path.name} is not an age v1 file")
    header = head.split(b"\n---", 1)[0]
    return sum(1 for line in header.split(b"\n") if line.startswith(b"-> X25519 "))


def run(argv: list[str], *, timeout: float = 600, check: bool = True, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(argv, input=input_bytes, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise DrillError(f"{argv[0]} not found", EXIT_SETUP) from None
    except subprocess.TimeoutExpired:
        raise DrillError(f"{argv[0]} timed out after {timeout:.0f} s") from None
    if check and result.returncode != 0:
        lines = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines() or [""]
        reason = next((line for line in lines if "error" in line.lower()), lines[-1])
        raise DrillError(f"{Path(argv[0]).name} {argv[1] if len(argv) > 1 else ''} failed (exit {result.returncode}): {reason[:200]}")
    return result


def check_meta(backup_dir: Path, recipients: int) -> dict:
    bundle, meta_path = backup_dir / BUNDLE_NAME, backup_dir / META_NAME
    for path in (bundle, meta_path):
        if not path.is_file():
            raise DrillError(f"{path} is missing (copy bundle.tar.age and meta.json of one backup)", EXIT_SETUP)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        expected = meta["encrypted"]
    except (ValueError, KeyError, TypeError):
        raise DrillError("meta.json is not an artesa-backup meta.json") from None
    size, digest = bundle.stat().st_size, sha256_file(bundle)
    if size != expected.get("size") or digest != expected.get("sha256"):
        raise DrillError(f"bundle.tar.age does not match meta.json (size {size} vs {expected.get('size')}, sha256 {digest[:16]} vs {str(expected.get('sha256'))[:16]})")
    stanzas = age_recipient_stanzas(bundle)
    if stanzas != recipients:
        raise DrillError(f"age header has {stanzas} X25519 recipient(s); expected {recipients}")
    say(f"  [PASS] bundle.tar.age matches meta.json ({size} bytes, sha256 {digest[:16]}...), age v1, {stanzas} recipients")
    return meta


def decrypt_and_extract(age: str, identity: Path, backup_dir: Path, work: Path, meta: dict) -> dict:
    tar_path = work / "bundle.tar"
    run([age, "--decrypt", "-i", str(identity), "-o", str(tar_path), str(backup_dir / BUNDLE_NAME)])
    os.chmod(tar_path, 0o600)
    say(f"  [PASS] decrypted with identity {identity.name}")
    extract = work / "bundle"
    extract.mkdir(mode=0o700)
    with tarfile.open(tar_path) as tar:
        names = tar.getnames()
        if any(n.startswith("/") or ".." in Path(n).parts for n in names):
            raise DrillError("bundle has unsafe member paths")
        tar.extractall(extract, filter="data")
    tar_path.unlink()
    for needed in ("database.dump", "manifest.json"):
        if not (extract / needed).is_file():
            raise DrillError(f"bundle has no {needed} (members: {sorted(names)})")
    manifest = json.loads((extract / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("backup_id") != meta.get("backup_id"):
        raise DrillError(f"manifest backup_id {manifest.get('backup_id')} != meta.json {meta.get('backup_id')}")
    expected = manifest["files"]["database.dump"]
    dump = extract / "database.dump"
    if dump.stat().st_size != expected["size"] or sha256_file(dump) != expected["sha256"]:
        raise DrillError("database.dump does not match the manifest checksum")
    say(f"  [PASS] bundle members {sorted(names)}; database.dump matches the manifest ({expected['size']} bytes)")
    return manifest


def check_finanzas(extract: Path, manifest: dict, export_to: Path | None) -> None:
    """docs/BACKUP.md §17: the Finanzas SQLite copy inside the bundle, when the
    backup has one: checksum, PRAGMA integrity_check and row counts against
    the manifest. With ``export_to`` the recovered file is copied there (0600)
    for an actual restore."""
    import sqlite3

    info = manifest.get("finanzas") or {"status": "not-configured"}
    if info.get("status") != "included":
        say(f"  [INFO] Finanzas: {info.get('status')} in this backup")
        if export_to is not None:
            raise DrillError("--export-finanzas was given but this backup has no Finanzas copy")
        return
    copy = extract / info["member"]
    if not copy.is_file() or copy.stat().st_size != info["size"] or sha256_file(copy) != info["sha256"]:
        raise DrillError("the Finanzas copy does not match the manifest checksum")
    conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise DrillError("the Finanzas copy fails PRAGMA integrity_check")
        counts = {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in info["table_counts"]}
    finally:
        conn.close()
    if counts != info["table_counts"]:
        raise DrillError(f"Finanzas row counts {counts} != manifest {info['table_counts']}")
    say(f"  [PASS] Finanzas: checksum, integrity_check and row counts match ({sum(counts.values())} rows)")
    if export_to is not None:
        if export_to.exists():
            raise DrillError(f"{export_to} already exists; choose a new path")
        shutil.copyfile(copy, export_to)
        os.chmod(export_to, 0o600)
        say(f"  [OK] Finanzas database written to {export_to}")


class Container:
    """A throw-away PostgreSQL with no network: reached only through docker exec."""

    def __init__(self, docker: str, image: str):
        self.docker, self.image = docker, image
        self.name = f"artesa-d10-drill-{secrets.token_hex(4)}"
        self.started = False

    def start(self) -> None:
        run([self.docker, "run", "-d", "--rm", "--name", self.name, "--network", "none",
             "-e", f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}", self.image], timeout=120)
        self.started = True
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            probe = run([self.docker, "exec", self.name, "pg_isready", "-U", "postgres", "-h", "/var/run/postgresql"], check=False, timeout=30)
            if probe.returncode == 0:
                # the entrypoint restarts the server once after initdb; confirm it answers a query
                if run(self.psql("select 1"), check=False, timeout=30).returncode == 0:
                    return
            time.sleep(1)
        raise DrillError("the throw-away PostgreSQL did not become ready", EXIT_SETUP)

    def psql(self, sql: str, db: str = "postgres") -> list[str]:
        return [self.docker, "exec", self.name, "psql", "-U", "postgres", "-d", db, "-XAtq", "-v", "ON_ERROR_STOP=1", "-c", sql]

    def query(self, sql: str, db: str = "postgres") -> str:
        return run(self.psql(sql, db), timeout=120).stdout.decode("utf-8").strip()

    def stop(self) -> None:
        if self.started:
            subprocess.run([self.docker, "rm", "-f", self.name], capture_output=True, timeout=60)
            self.started = False


def restore_test(docker: str, image: str, dump: Path, manifest: dict) -> None:
    container = Container(docker, image)
    try:
        container.start()
        major = int(container.query("show server_version_num")) // 10000
        wanted = int(manifest["db"].get("server_major") or 0)
        if major < wanted:
            raise DrillError(f"{image} is PostgreSQL {major}; the backup comes from {wanted}. Use POSTGRES_IMAGE=postgres:{wanted}", EXIT_SETUP)
        container.query("create database drill")
        with open(dump, "rb") as handle:
            run([docker, "exec", "-i", container.name, "pg_restore", "-U", "postgres", "-d", "drill", "--no-owner", "--no-privileges",
                 "--exit-on-error"], input_bytes=handle.read(), timeout=900)
        revision = container.query("select version_num from alembic_version", "drill")
        if revision != manifest["db"]["alembic_revision"]:
            raise DrillError(f"alembic revision {revision} != manifest {manifest['db']['alembic_revision']}")
        expected = manifest["db"]["table_counts"]
        restored = {}
        for table in sorted(expected):
            if not table.replace("_", "").isalnum():
                raise DrillError(f"unexpected table name in manifest: {table!r}")
            restored[table] = int(container.query(f'select count(*) from public."{table}"', "drill"))
        mismatched = {t: (restored[t], expected[t]) for t in expected if restored[t] != expected[t]}
        if mismatched:
            raise DrillError(f"row counts differ from the manifest: {mismatched}")
        say(f"  [PASS] restored into a throw-away PostgreSQL {major} (no network): alembic {revision}, "
            f"{len(expected)} tables, counts identical ({sum(expected.values())} rows)")
    finally:
        container.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="off-host recovery drill for one encrypted artesa-backup backup (D10)")
    parser.add_argument("backup_dir", type=Path, help="directory with bundle.tar.age and meta.json of ONE backup")
    parser.add_argument("identity", type=Path, help="age identity file (K1 or K2) on this trusted machine")
    parser.add_argument("--recipients", type=int, default=2, help="expected X25519 recipients in the header (default 2: K1+K2)")
    parser.add_argument("--export-finanzas", type=Path, default=None,
                        help="also write the recovered Finanzas SQLite to this NEW path (docs/BACKUP.md §17)")
    args = parser.parse_args(argv)
    age = os.environ.get("AGE", "age")
    docker = os.environ.get("DOCKER", "docker")
    image = os.environ.get("POSTGRES_IMAGE", "postgres:18")
    os.umask(0o077)
    work = Path(tempfile.mkdtemp(prefix="artesa-d10-drill-"))
    say(f"D10 off-host drill: {args.backup_dir} with {args.identity.name}")
    try:
        if not args.identity.is_file():
            raise DrillError(f"identity {args.identity} not found", EXIT_SETUP)
        if shutil.which(age) is None and not Path(age).is_file():
            raise DrillError(f"age binary not found ({age}); install age on THIS machine (never on the server)", EXIT_SETUP)
        meta = check_meta(args.backup_dir, args.recipients)
        manifest = decrypt_and_extract(age, args.identity, args.backup_dir, work, meta)
        check_finanzas(work / "bundle", manifest, args.export_finanzas)
        restore_test(docker, image, work / "bundle" / "database.dump", manifest)
        say(f"DRILL PASS: backup {meta['backup_id']} recovered with {args.identity.name}")
        return EXIT_OK
    except DrillError as exc:
        say(f"DRILL {'FAIL' if exc.code == EXIT_FAIL else 'SETUP ERROR'}: {exc}")
        return exc.code
    except KeyboardInterrupt:
        say("DRILL INTERRUPTED")
        return EXIT_SETUP
    finally:
        shutil.rmtree(work, ignore_errors=True)
        say(f"cleanup: plaintext work dir removed: {not work.exists()}")


if __name__ == "__main__":
    sys.exit(main())
