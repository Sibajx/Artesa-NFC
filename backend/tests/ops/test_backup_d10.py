"""D10.1 (#126): artesa-backup, local encrypted backup foundation.

The real ``artesa_backup`` code runs against the fake World of helpers.py:
pg_dump / pg_restore / initdb / db-state are answered by FakeRunner, and a
fake ``age`` writes a structurally valid age v1 file (one X25519 stanza per
recipient). The real ``age`` binary is exercised by the D10 rehearsal.
"""
from __future__ import annotations

import base64
import json
import os
import stat
from datetime import timedelta
from pathlib import Path

import pytest

import artesa_backup as ab
import deploy_layout as dl
import release_common as rc
import release_probe as rp
from tests.ops.helpers import CANARY_PASSWORD, DATABASE_URL, NOW, Scenario

BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def recipient(seed: int) -> str:
    return "age1" + "".join(BECH32[(seed * 7 + i * 3) % 32] for i in range(58))


K1, K2 = recipient(1), recipient(2)


class FakeAge:
    """Answers ``age --version`` and ``age --encrypt -r .. -r .. -o out in``."""

    def __init__(self, *, present: bool = True, fail: bool = False, garbage: bool = False, stanzas: int | None = None) -> None:
        self.present, self.fail, self.garbage, self.stanzas = present, fail, garbage, stanzas
        self.calls: list[list[str]] = []

    def __call__(self, argv, env, cwd):
        if os.path.basename(argv[0]) != "age":
            return None
        self.calls.append(argv)
        if not self.present:
            return rp.RunResult(127, "", "command not found: age")
        if "--version" in argv:
            return rp.RunResult(0, "v1.2.1\n")
        if self.fail:
            return rp.RunResult(1, "", "age: error: failed to encrypt")
        out = Path(argv[argv.index("-o") + 1])
        plain = Path(argv[-1]).read_bytes()
        n = self.stanzas if self.stanzas is not None else argv.count("-r")
        b64 = lambda s: base64.b64encode(s.encode().ljust(32, b"x")[:32]).decode().rstrip("=")
        header = b"age-encryption.org/v1\n" + b"".join(f"-> X25519 {b64(f'r{i}')}\n{b64(f'body{i}')}\n".encode() for i in range(n))
        header += f"--- {b64('mac')}\n".encode()
        out.write_bytes(b"garbage-not-age" if self.garbage else header + bytes(b ^ 0x5A for b in plain))
        return rp.RunResult(0)


def setup(s: Scenario, *, recipients=(K1, K2), mode: int = 0o600) -> None:
    base = s.root / "shared" / "backup"
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    lines = "".join(f"{k}={v}\n" for k, v in zip(ab.RECIPIENT_KEYS, recipients) if v)
    (base / "backup.env").write_text(lines)
    os.chmod(base / "backup.env", mode)


@pytest.fixture
def s(tmp_path):
    sc = Scenario(tmp_path)
    sc.release("r1")
    sc.activate("r1")
    setup(sc)
    return sc


def ctx(s, *, age: FakeAge | None = None, clock=lambda: NOW, **kw):
    context = s.ctx(clock=clock)
    runner = context.runner
    runner.handlers = [age or FakeAge()]
    out = s.sink
    return ab.Context(root=s.root, rehearsal=True, runner=runner, clock=clock, sleep=lambda x: None, out=out,
                      hostname=lambda: "rehearsal-host", deploy_lock_wait=kw.pop("deploy_lock_wait", 0.0), **kw)


def run(s, *argv, **kw):
    c = ctx(s, **kw)
    s.last_backup_ctx = c
    return ab.main(["--root", str(s.root), "--rehearsal", *argv], c)


def layout(s) -> ab.BackupLayout:
    return ab.BackupLayout(s.root)


def backups(s) -> list[Path]:
    return sorted(p for p in layout(s).encrypted.iterdir() if not p.name.startswith("."))


def state(s) -> dict:
    return json.loads(layout(s).status_file.read_text())


def plaintext_anywhere(s) -> list[str]:
    """Any file under shared/backup/ whose bytes are a pg custom dump or an
    uncompressed tar -- i.e. plaintext database content."""
    found = []
    for path in layout(s).base.rglob("*"):
        if path.is_file():
            head = path.read_bytes()[:512]
            if head.startswith(b"PGDMP") or b"ustar" in head[257:265] or path.suffix in (".dump", ".tar"):
                found.append(str(path.relative_to(s.root)))
    return found


# --- run: success ----------------------------------------------------------------------------------------------

def test_run_success_encrypts_to_two_recipients_and_keeps_no_plaintext(s):
    age = FakeAge()
    assert run(s, "run", age=age) == 0
    [b] = backups(s)
    assert sorted(p.name for p in b.iterdir()) == ["bundle.tar.age", "meta.json"]
    assert stat.S_IMODE(b.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in b.iterdir())
    enc = [c for c in age.calls if "--encrypt" in c][0]
    assert enc.count("-r") == 2 and K1 in enc and K2 in enc
    assert plaintext_anywhere(s) == [] and list(layout(s).staging.iterdir()) == []
    assert "OFFSITE: NOT CONFIGURED -- D10: INCOMPLETE" in s.sink.text


def test_backup_id_and_public_meta(s):
    assert run(s, "run") == 0
    [b] = backups(s)
    commit = json.loads((s.root / "releases" / s.ids["r1"] / "RELEASE.json").read_text())["git"]["commit"]
    assert b.name == f"20260921T030000Z-{commit[:12]}"
    meta = json.loads((b / "meta.json").read_text())
    assert meta["encrypted"]["sha256"] == rc.sha256_file(str(b / "bundle.tar.age"))
    assert meta["encrypted"]["size"] == (b / "bundle.tar.age").stat().st_size
    assert meta["encryption"] == {"algorithm": "age", "recipient_type": "X25519", "recipients": [K1, K2]}
    assert meta["remote"] == {"status": "not-configured"} and meta["restore_check"]["ok"] is True
    assert "db" not in meta and "application" not in meta  # full manifest only inside the ciphertext


def test_full_manifest_inside_the_bundle(s):
    import tarfile
    assert run(s, "run") == 0
    [b] = backups(s)
    data = (b / "bundle.tar.age").read_bytes()
    plain = bytes(x ^ 0x5A for x in data[data.index(b"--- "):].split(b"\n", 1)[1])  # FakeAge "decrypts" by xor
    import io
    with tarfile.open(fileobj=io.BytesIO(plain)) as tar:
        names = tar.getnames()
        manifest = json.loads(tar.extractfile("manifest.json").read())
    assert {"database.dump", "manifest.json", "recovery/RELEASE.json"} <= set(names)
    assert manifest["schema_version"] == 1 and manifest["hostname"] == "rehearsal-host"
    assert manifest["application"]["release_id"] == s.ids["r1"] and manifest["application"]["git_commit"]
    assert manifest["db"]["engine"] == "postgresql" and manifest["db"]["alembic_revision"] == "bbb222"
    assert manifest["db"]["table_counts"] == {"artisan": 2, "piece": 5} and manifest["db"]["dump_format"].startswith("custom")
    assert manifest["files"]["database.dump"]["sha256"] and manifest["encryption"]["recipients"] == [K1, K2]
    assert manifest["restore_check"]["ok"] and manifest["restore_check"]["counts_match"] and manifest["remote"] == {"status": "not-configured"}
    assert manifest["backup_tool_version"] == rc.TOOL_VERSION
    text = json.dumps(manifest)
    assert DATABASE_URL not in text and CANARY_PASSWORD not in text and "AGE-SECRET-KEY" not in text


def test_restore_check_runs_on_a_disposable_target_before_encryption(s):
    age = FakeAge()
    assert run(s, "run", age=age) == 0
    argv = [" ".join(a) for a in s.world.argv_log]
    restore_at = next(i for i, a in enumerate(argv) if os.path.basename(a.split()[0]) == "pg_restore" and "--dbname=" in a)
    encrypt_at = next(i for i, a in enumerate(argv) if "--encrypt" in a)
    assert restore_at < encrypt_at
    assert s.world.scratch_created == 1 and s.world.scratch_destroyed >= 1 and s.world.scratch is None
    assert state(s)["last_restore_check"]["ok"] is True


def test_pg_dump_credentials_never_in_argv_and_log_is_clean(s):
    assert run(s, "run") == 0
    assert not any(CANARY_PASSWORD in " ".join(a) for a in s.world.argv_log)
    log = layout(s).log_file.read_text()
    assert CANARY_PASSWORD not in log and DATABASE_URL not in log
    assert all(set(json.loads(line)) <= ab.ALLOWED_LOG_KEYS for line in log.splitlines())


def test_scheduled_run_needs_no_tty(s):
    c = ctx(s)
    assert ab.main(["--root", str(s.root), "--rehearsal", "run", "--scheduled"], c) == 0


# --- run: failures leave nothing ------------------------------------------------------------------------------------

def assert_failed_cleanly(s, code_seen: int, expected_code: int, text: str):
    assert code_seen == expected_code
    assert backups(s) == [] and plaintext_anywhere(s) == [] and list(layout(s).staging.iterdir()) == []
    st = state(s)
    assert st["last_result"] in ("failure", "deferred") and st["consecutive_failures"] == 1 and text in st["last_error"]["message"]
    assert CANARY_PASSWORD not in json.dumps(st)


def test_pg_dump_failure(s):
    s.world.pg_dump_ok = False
    assert_failed_cleanly(s, run(s, "run"), rc.Exit.BACKUP, "pg_dump failed")


def test_pg_dump_timeout(s):
    s.world.pg_dump_ok = True
    timeout = lambda argv, env, cwd: rp.RunResult(124, "", "timed out after 1800s: pg_dump") if os.path.basename(argv[0]) == "pg_dump" and "--version" not in argv else None
    c = ctx(s); c.runner.handlers.insert(0, timeout)
    assert_failed_cleanly(s, ab.main(["--root", str(s.root), "--rehearsal", "run"], c), rc.Exit.BACKUP, "pg_dump failed (exit 124)")


def test_empty_dump_is_refused(s):
    s.world.pg_dump_empty = True
    assert_failed_cleanly(s, run(s, "run"), rc.Exit.BACKUP, "empty")


def test_restore_check_failure_keeps_no_backup(s):
    s.world.restore_ok = False
    assert_failed_cleanly(s, run(s, "run"), rc.Exit.BACKUP, "restore-check of the fresh dump failed")
    assert s.world.scratch is None  # disposable target destroyed anyway


def test_restore_check_wrong_counts(s):
    s.world.restore_drops_rows = True
    assert_failed_cleanly(s, run(s, "run"), rc.Exit.BACKUP, "row counts differ")


def test_restore_check_bad_alembic_revision(s):
    orig = s.world.row_counts
    def wrong_rev(argv, env, cwd):
        if argv[-1] == "db-state" and "artesa_restore_test_" in env.get("DATABASE_URL", "") and s.world.scratch and s.world.scratch.get("rows"):
            return rp.RunResult(0, json.dumps({"server_version_num": 180006, "alembic_versions": ["zzz999"], "row_counts": orig}))
        return None
    c = ctx(s); c.runner.handlers.insert(0, wrong_rev)
    assert_failed_cleanly(s, ab.main(["--root", str(s.root), "--rehearsal", "run"], c), rc.Exit.BACKUP, "Alembic revision differs")


@pytest.mark.parametrize("recipients, text", [((None, K2), "ARTESA_BACKUP_AGE_RECIPIENT_K1 is missing"),
                                              ((K1, None), "ARTESA_BACKUP_AGE_RECIPIENT_K2 is missing"),
                                              ((K1, K1), "must be different"),
                                              ((K1, "age1notvalid"), "not an age X25519 public recipient")])
def test_recipient_configuration_is_mandatory(s, recipients, text):
    setup(s, recipients=recipients)
    assert_failed_cleanly(s, run(s, "run"), rc.Exit.CONFIG, text)
    assert not any(os.path.basename(a[0]) == "pg_dump" and "--version" not in a for a in s.world.argv_log)  # nothing dumped


def test_missing_config_file_and_loose_mode(s):
    (layout(s).config).unlink()
    assert_failed_cleanly(s, run(s, "run"), rc.Exit.CONFIG, "backup.env not found")
    setup(s, mode=0o644)
    assert run(s, "run") == rc.Exit.CONFIG and "must be 0600" in state(s)["last_error"]["message"]


def test_a_private_key_in_the_config_is_refused(s):
    setup(s)
    with open(layout(s).config, "a") as fh:
        fh.write("# AGE-SECRET-KEY-1QQQQ\n")
    assert_failed_cleanly(s, run(s, "run"), rc.Exit.CONFIG, "PRIVATE key")


def test_age_missing(s):
    assert_failed_cleanly(s, run(s, "run", age=FakeAge(present=False)), rc.Exit.CONFIG, "'age' binary is not available")


def test_encryption_failure(s):
    assert_failed_cleanly(s, run(s, "run", age=FakeAge(fail=True)), rc.Exit.BACKUP, "age encryption failed")


@pytest.mark.parametrize("age, text", [(FakeAge(garbage=True), "bad magic"), (FakeAge(stanzas=1), "1 X25519 recipient stanza")])
def test_invalid_ciphertext_is_refused(s, age, text):
    assert_failed_cleanly(s, run(s, "run", age=age), rc.Exit.BACKUP, text)


def test_consecutive_failures_then_recovery(s):
    s.world.pg_dump_ok = False
    run(s, "run"); run(s, "run", clock=lambda: NOW + timedelta(minutes=1))
    assert state(s)["consecutive_failures"] == 2
    s.world.pg_dump_ok = True
    assert run(s, "run", clock=lambda: NOW + timedelta(minutes=2)) == 0
    st = state(s)
    assert st["consecutive_failures"] == 0 and st["last_error"] is None and st["last_result"] == "success"


def test_leftover_staging_from_a_killed_run_is_removed(s):
    stale = layout(s).staging / "20260920T000000Z-nocommit.tmp-999"
    layout(s).ensure()
    stale.mkdir()
    (stale / "database.dump").write_bytes(b"PGDMP-old-plaintext")
    (layout(s).encrypted / ".20260920T000000Z-nocommit.partial").mkdir()
    assert run(s, "run") == 0
    assert not stale.exists() and plaintext_anywhere(s) == [] and "leftover staging" in s.sink.text


# --- locks -----------------------------------------------------------------------------------------------------------

def test_backup_lock_conflict(s):
    import fcntl
    layout(s).ensure()
    fd = os.open(layout(s).lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert run(s, "run") == rc.Exit.PREFLIGHT
    finally:
        os.close(fd)
    assert backups(s) == [] and "holds the backup lock" in s.sink.text
    assert not layout(s).status_file.exists()  # a concurrent run owns the state


def test_deploy_lock_busy_waits_then_proceeds(s, monkeypatch):
    real = dl.deploy_lock
    calls = {"n": 0}
    def flaky(layout_):
        calls["n"] += 1
        if calls["n"] == 1:
            raise rc.OpsError(rc.Exit.PREFLIGHT, "another deploy operation holds the deployment lock")
        return real(layout_)
    monkeypatch.setattr(ab.dl, "deploy_lock", flaky)
    assert run(s, "run", deploy_lock_wait=60.0) == 0 and calls["n"] == 2 and "deploy lock busy" in s.sink.text


def test_deploy_lock_held_too_long_defers(s, monkeypatch):
    def busy(layout_):
        raise rc.OpsError(rc.Exit.PREFLIGHT, "another deploy operation holds the deployment lock")
    monkeypatch.setattr(ab.dl, "deploy_lock", busy)
    assert_failed_cleanly(s, run(s, "run", deploy_lock_wait=0.0), rc.Exit.PREFLIGHT, "backup deferred")
    assert state(s)["last_result"] == "deferred"


def test_the_deploy_lock_is_held_only_around_pg_dump(s, monkeypatch):
    held = {"during_dump": None, "during_age": None}
    real = dl.deploy_lock
    def probe(argv, env, cwd):
        name = os.path.basename(argv[0])
        if name == "pg_dump" and "--version" not in argv:
            held["during_dump"] = dl.lock_is_held(dl.Layout(s.root))
        if name == "age" and "--encrypt" in argv:
            held["during_age"] = dl.lock_is_held(dl.Layout(s.root))
        return None
    c = ctx(s); c.runner.handlers.insert(0, probe)
    assert ab.main(["--root", str(s.root), "--rehearsal", "run"], c) == 0
    assert held == {"during_dump": True, "during_age": False}


# --- retention ---------------------------------------------------------------------------------------------------------

def make_runs(s, n):
    for i in range(n):
        assert run(s, "run", clock=lambda i=i: NOW + timedelta(days=i)) == 0


def test_retention_keeps_seven(s):
    make_runs(s, 9)
    names = [b.name[:16] for b in backups(s)]
    assert len(names) == 7 and names[0] == "20260923T030000Z" and names[-1] == "20260929T030000Z"
    assert state(s)["local_backups"] == 7


def test_retention_never_touches_unknown_entries_or_shared_backups(s):
    layout(s).ensure()
    foreign = layout(s).encrypted / "hand-made-copy"
    foreign.mkdir()
    n08 = s.root / "shared" / "backups" / "artesa-nfc-20260101T000000Z-bbb222.dump"
    n08.write_bytes(b"PGDMP-n08")
    make_runs(s, 9)
    assert foreign.is_dir() and n08.exists()


def test_retention_protects_the_last_valid_backup(s):
    make_runs(s, 8)
    [oldest, *newer] = backups(s)[-8:] if len(backups(s)) >= 8 else backups(s)
    # corrupt every backup except the oldest: the oldest is the last valid one
    for b in backups(s)[1:]:
        (b / "bundle.tar.age").write_bytes(b"corrupted")
    tool = ab.Tool(ctx(s))
    assert tool._retention(keep=7) == []  # nothing deleted: the only valid one is protected
    assert backups(s)[0].name == oldest.name


def test_retention_failure_is_a_warning_and_keeps_the_new_backup(s, monkeypatch):
    monkeypatch.setattr(ab.Tool, "_retention", lambda self, **kw: (_ for _ in ()).throw(OSError("disk")))
    assert run(s, "run") == 0
    assert len(backups(s)) == 1 and state(s)["retention_warning"] and "retention failed" in s.sink.text


# --- status -------------------------------------------------------------------------------------------------------------

def test_status_after_success_and_json(s):
    assert run(s, "run") == 0
    assert run(s, "status", clock=lambda: NOW + timedelta(hours=2)) == 0
    text = s.sink.text
    assert "OFFSITE: NOT CONFIGURED" in text and "D10: INCOMPLETE" in text and "restore-check     PASS" in text and "STALE" not in text
    assert run(s, "status", "--json", clock=lambda: NOW + timedelta(hours=2)) == 0
    report = json.loads(s.sink.text)
    assert report["d10"] == "INCOMPLETE" and report["offsite_status"] == "not-configured" and report["stale"] is False
    assert report["last_success_age_hours"] == 2.0 and report["local_backups"] == 1 and report["encryption_status"] == "configured"


def test_status_stale_after_26_hours_and_before_any_run(s):
    assert run(s, "status") == rc.Exit.PREFLIGHT and "STALE" in s.sink.text  # never ran
    assert run(s, "run") == 0
    assert run(s, "status", clock=lambda: NOW + timedelta(hours=27)) == rc.Exit.PREFLIGHT and "STALE" in s.sink.text


def test_status_reports_failures(s):
    s.world.pg_dump_ok = False
    run(s, "run")
    assert run(s, "status") == rc.Exit.PREFLIGHT
    assert "failures in a row 1" in s.sink.text and "pg_dump failed" in s.sink.text


def test_state_is_written_atomically_and_privately(s, monkeypatch):
    assert run(s, "run") == 0
    assert stat.S_IMODE(layout(s).status_file.stat().st_mode) == 0o600
    assert not [p for p in layout(s).state_dir.iterdir() if p.name.startswith(".")]
    replaced = []
    real = os.replace
    monkeypatch.setattr(ab.os, "replace", lambda a, b: (replaced.append(Path(b).name), real(a, b))[1])
    assert run(s, "run", clock=lambda: NOW + timedelta(minutes=5)) == 0
    assert "status.json" in replaced


# --- verify / restore-test ---------------------------------------------------------------------------------------------

def test_verify_pass_and_detects_tampering(s):
    make_runs(s, 2)
    assert run(s, "verify", "--all") == 0 and s.sink.text.count("[PASS]") == 2
    newest = backups(s)[-1]
    data = bytearray((newest / "bundle.tar.age").read_bytes()); data[-1] ^= 1
    (newest / "bundle.tar.age").write_bytes(bytes(data))
    assert run(s, "verify") == rc.Exit.ARTIFACT_INVALID and "sha256 differs" in s.sink.text


def test_verify_detects_wrong_recipient_count_and_loose_modes(s):
    assert run(s, "run") == 0
    b = backups(s)[0]
    os.chmod(b / "meta.json", 0o644)
    assert run(s, "verify", b.name) == rc.Exit.ARTIFACT_INVALID and "readable by others" in s.sink.text


def test_restore_test_on_an_explicit_plaintext_dump(s):
    import deploy_db as ddb
    env = rp.read_env_file(s.root / "shared" / ".env")
    state_ = ddb.read_db_state(s.ctx().runner, s.root / "releases" / s.ids["r1"], rp.child_env(env.values))
    res = ddb.create_backup(runner=s.ctx().runner, layout=dl.Layout(s.root), env_file=env, state=state_, active_release=s.ids["r1"], clock=lambda: NOW)
    assert run(s, "restore-test", str(res.path)) == 0 and "restore-test PASS" in s.sink.text
    assert s.world.scratch is None


def test_restore_test_refuses_a_path_without_metadata(s, tmp_path):
    bogus = tmp_path / "x.dump"; bogus.write_bytes(b"PGDMP-no-sidecar")
    assert run(s, "restore-test", str(bogus)) == rc.Exit.BACKUP


def test_pre_migration_backup_of_the_deploy_tool_is_unchanged(s):
    import deploy_db as ddb
    env = rp.read_env_file(s.root / "shared" / ".env")
    st = ddb.read_db_state(s.ctx().runner, s.root / "releases" / s.ids["r1"], rp.child_env(env.values))
    res = ddb.create_backup(runner=s.ctx().runner, layout=dl.Layout(s.root), env_file=env, state=st, active_release=s.ids["r1"], clock=lambda: NOW,
                            active_commit="a" * 40)
    assert res.path.parent == s.root / "shared" / "backups" and res.path.name.endswith("-aaaaaaaaaaaa.dump")
    assert stat.S_IMODE(res.path.stat().st_mode) == 0o600
