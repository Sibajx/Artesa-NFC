#!/usr/bin/env python3
"""Self-test of drill.py with a synthetic backup in the artesa-backup format.

Builds, in a private temp dir: three throw-away age identities (K1, K2 and an
unrelated K3), a small database in a throw-away PostgreSQL container (no
network) with an alembic_version row and two tables, a ``pg_dump -Fc`` of it,
the manifest/bundle/meta.json layout that ``artesa-backup run`` writes
(backend/ops/artesa_backup.py), encrypted to K1+K2. Then it runs drill.py:

  PASS expected: K1; K2
  FAIL expected: K3 (not a recipient); meta.json sha256 tampered; bundle
                 re-encrypted with a manifest whose counts are wrong; wrong
                 --recipients

Nothing real is used: no server, no real keys, no production data. Everything
is removed on exit. Needs: age + age-keygen (AGE, AGE_KEYGEN), docker,
POSTGRES_IMAGE (default postgres:18).

    python3 qa/d10-offhost-drill/selftest.py
"""

from __future__ import annotations

import hashlib
import io
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

HERE = Path(__file__).resolve().parent
AGE = os.environ.get("AGE", "age")
AGE_KEYGEN = os.environ.get("AGE_KEYGEN", "age-keygen")
DOCKER = os.environ.get("DOCKER", "docker")
IMAGE = os.environ.get("POSTGRES_IMAGE", "postgres:18")
REVISION = "895974720462"
COUNTS = {"artisan": 3, "piece": 4}


def sh(argv, **kw):
    return subprocess.run(argv, check=True, capture_output=True, **kw)


def keygen(path: Path) -> str:
    sh([AGE_KEYGEN, "-o", str(path)])
    return sh([AGE_KEYGEN, "-y", str(path)]).stdout.decode().strip()


def make_dump(out: Path) -> int:
    name = f"artesa-d10-selftest-{secrets.token_hex(4)}"
    sh([DOCKER, "run", "-d", "--rm", "--name", name, "--network", "none", "-e", f"POSTGRES_PASSWORD={secrets.token_urlsafe(16)}", IMAGE])
    try:
        psql = [DOCKER, "exec", name, "psql", "-U", "postgres", "-XAtq", "-v", "ON_ERROR_STOP=1", "-c"]
        for _ in range(90):
            if subprocess.run(psql + ["select 1"], capture_output=True).returncode == 0:
                break
            time.sleep(1)
        time.sleep(2)  # the entrypoint restarts the server once after initdb
        for _ in range(30):
            if subprocess.run(psql + ["select 1"], capture_output=True).returncode == 0:
                break
            time.sleep(1)
        sh(psql + [f"create table alembic_version (version_num varchar(32) primary key); insert into alembic_version values ('{REVISION}');"
                   "create table artisan (id serial primary key, name text); insert into artisan (name) select 'a'||g from generate_series(1,3) g;"
                   "create table piece (id serial primary key, artisan_id int references artisan(id)); insert into piece (artisan_id) values (1),(1),(2),(3);"])
        major = int(sh(psql + ["show server_version_num"]).stdout.decode().strip()) // 10000
        dump = sh([DOCKER, "exec", name, "pg_dump", "-U", "postgres", "-Fc", "--no-owner", "--no-privileges", "postgres"]).stdout
        out.write_bytes(dump)
        return major
    finally:
        subprocess.run([DOCKER, "rm", "-f", name], capture_output=True)


def build_backup(base: Path, dump: bytes, major: int, recipients: list[str], counts: dict, backup_id: str) -> Path:
    """Same layout as artesa_backup._pipeline/_make_bundle: tar(database.dump,
    manifest.json, recovery/*) -> age to K1+K2 -> <id>/{bundle.tar.age, meta.json}."""
    manifest = {"schema_version": 1, "backup_id": backup_id, "created_at": "2026-09-28T09:30:00Z", "hostname": "selftest", "backup_tool_version": "1.3.1",
                "application": {"release_id": "selftest", "git_commit": "0" * 40},
                "db": {"engine": "postgresql", "server_version": major * 10000, "server_major": major, "pg_dump_version": "selftest",
                       "dump_format": "custom (pg_dump -Fc --no-owner --no-privileges)", "alembic_revision": REVISION, "table_counts": counts,
                       "roles_and_grants": "not included"},
                "files": {"database.dump": {"size": len(dump), "sha256": hashlib.sha256(dump).hexdigest()}},
                "restore_check": {"ok": True}, "encryption": {"algorithm": "age", "recipient_type": "X25519", "recipients": recipients},
                "remote": {"status": "not-configured"}}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in (("database.dump", dump), ("manifest.json", json.dumps(manifest).encode()), ("recovery/RELEASE.json", b"{}")):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            tar.addfile(info, io.BytesIO(data))
    directory = base / backup_id
    directory.mkdir(mode=0o700)
    plain = base / f"{backup_id}.tar"
    plain.write_bytes(buf.getvalue())
    argv = [AGE, "--encrypt"] + [a for r in recipients for a in ("-r", r)] + ["-o", str(directory / "bundle.tar.age"), str(plain)]
    sh(argv)
    plain.unlink()
    enc = (directory / "bundle.tar.age").read_bytes()
    meta = {"schema_version": 1, "backup_id": backup_id, "encrypted": {"name": "bundle.tar.age", "size": len(enc), "sha256": hashlib.sha256(enc).hexdigest()},
            "encryption": {"algorithm": "age", "recipients": recipients}, "restore_check": {"ok": True}, "remote": {"status": "not-configured"}}
    (directory / "meta.json").write_text(json.dumps(meta))
    return directory


def drill(backup: Path, identity: Path, *extra: str) -> tuple[int, str]:
    result = subprocess.run([sys.executable, str(HERE / "drill.py"), str(backup), str(identity), *extra], capture_output=True, text=True,
                            env={**os.environ, "AGE": AGE, "DOCKER": DOCKER, "POSTGRES_IMAGE": IMAGE}, timeout=600)
    return result.returncode, result.stdout


def main() -> int:
    os.umask(0o077)
    base = Path(tempfile.mkdtemp(prefix="artesa-d10-selftest-"))
    results: list[tuple[str, bool, str]] = []
    try:
        k1, k2, k3 = (keygen(base / f"K{i}.key") for i in (1, 2, 3))
        major = make_dump(base / "db.dump")
        dump = (base / "db.dump").read_bytes()
        good = build_backup(base, dump, major, [k1, k2], COUNTS, "20260928T093000Z-000000000001")
        tampered_meta = build_backup(base, dump, major, [k1, k2], COUNTS, "20260928T093000Z-000000000002")
        meta = json.loads((tampered_meta / "meta.json").read_text())
        meta["encrypted"]["sha256"] = "0" * 64
        (tampered_meta / "meta.json").write_text(json.dumps(meta))
        wrong_counts = build_backup(base, dump, major, [k1, k2], {"artisan": 3, "piece": 5}, "20260928T093000Z-000000000003")

        cases = [
            ("K1 recovers the backup", good, base / "K1.key", (), 0, "DRILL PASS"),
            ("K2 recovers the backup independently", good, base / "K2.key", (), 0, "DRILL PASS"),
            ("K3 (not a recipient) cannot decrypt", good, base / "K3.key", (), 1, "age --decrypt failed"),
            ("tampered meta.json sha256 is refused before decrypting", tampered_meta, base / "K1.key", (), 1, "does not match meta.json"),
            ("manifest counts that do not match the restore fail", wrong_counts, base / "K1.key", (), 1, "row counts differ"),
            ("unexpected recipient count is refused", good, base / "K1.key", ("--recipients", "3"), 1, "expected 3"),
        ]
        for label, backup, identity, extra, want_code, want_text in cases:
            code, out = drill(backup, identity, *extra)
            ok = code == want_code and want_text in out and "plaintext work dir removed: True" in out
            results.append((label, ok, f"exit {code}; " + (out.strip().splitlines()[-2] if len(out.strip().splitlines()) > 1 else out.strip())))
        leftovers = subprocess.run([DOCKER, "ps", "-a", "--filter", "name=artesa-d10-", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
        results.append(("no drill or self-test container left behind", not leftovers, f"{leftovers}"))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    for label, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}  -- {detail[:160]}")
    passed = sum(ok for _, ok, _ in results)
    print(f"D10 OFF-HOST DRILL SELF-TEST: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
