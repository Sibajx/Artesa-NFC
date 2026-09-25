"""NEGATIVE CONTROLS (N-08, ADR-027): each test deliberately breaks one thing a
safety control protects and asserts the control notices. A control that cannot
fail proves nothing; docs/DEPLOYMENT.md section 14 maps every row to this file.

  NC-01 leaked .env            NC-09 backup that does not restore
  NC-02 secret in argv         NC-10 breaking migration
  NC-03 broken manifest        NC-11 failed candidate
  NC-04 wrong artifact         NC-12 failed restart (activation) simulation
  NC-05 missing module         NC-13 incompatible rollback
  NC-06 DB revision unknown    NC-14 malformed release id
  NC-07 wrong port owner       NC-15 archive traversal
  NC-08 migration without backup
"""
import io
import tarfile

import pytest

import artesa_deploy as ad
import deploy_db as ddb
import release_artifact as ra
import release_common as rc
from tests.ops.helpers import DATABASE_URL, NOW, Scenario, build, make_repo, migration_files, rewrite_tar


@pytest.fixture()
def mig(tmp_path):
    s = Scenario(tmp_path)
    s.release("r1"); s.activate("r1")
    s.release("r_add", migration_files("ccc333", "bbb222", "additive"))
    return s


def dep(s, name, *flags, **kw):
    return s.run(["deploy", s.ids[name], *flags], answers=[s.ids[name]], **kw)


def test_nc01_leaked_env_in_the_tree_or_the_archive(tmp_path):
    repo = make_repo(tmp_path, {"backend/app/.env": "DATABASE_URL=x\n"})
    with pytest.raises(rc.OpsError, match="forbidden file name"):
        build(repo, tmp_path / "o", ref="HEAD", rehearsal=True)
    clean = build(make_repo(tmp_path / "c"), tmp_path / "c" / "o")
    def add_env(m): m[".env"] = b"DATABASE_URL=x\n"
    rewrite_tar(clean.artifact, add_env)
    with pytest.raises(rc.OpsError):
        ra.verify_artifact(clean.artifact)


def test_nc02_secret_in_argv_is_refused_before_anything_runs(tmp_path):
    from tests.ops.test_ops_secrets import test_the_guard_refuses_a_tool_bug_that_would_put_a_secret_in_argv as control
    control(tmp_path)


def test_nc03_broken_manifest(tmp_path):
    art = build(make_repo(tmp_path), tmp_path / "o")
    rewrite_tar(art.artifact, lambda m: m.__setitem__("MANIFEST.sha256", m["MANIFEST.sha256"][:-10]))
    with pytest.raises(rc.OpsError):
        ra.verify_artifact(art.artifact)


def test_nc04_wrong_artifact(tmp_path):
    a = build(make_repo(tmp_path / "a"), tmp_path / "in")
    with pytest.raises(rc.OpsError, match="does not match the requested release id"):
        ra.verify_artifact(a.artifact, expected_id="20260101T000000Z-aaaaaaaaaaaa")
    b = build(make_repo(tmp_path / "b"), tmp_path / "in2", when=NOW.replace(hour=5))
    b.artifact.write_bytes(a.artifact.read_bytes())                 # b's name, a's bytes, a's sidecar hash
    b.sidecar.write_text(f"{rc.sha256_file(str(b.artifact))}  {b.artifact.name}\n")
    with pytest.raises(rc.OpsError, match="release_id"):
        ra.verify_artifact(b.artifact)


def test_nc05_missing_module_in_the_venv(tmp_path):
    from tests.ops.test_ops_incident_regression import test_2_a_shared_venv_missing_sqlalchemy_is_caught_by_prepare_and_by_verify_deep as control
    control(tmp_path)


def test_nc06_database_revision_unknown(mig):
    mig.world.db_revision = "0badc0ffee00"
    assert dep(mig, "r_add", "--dry-run", tty=False) == rc.Exit.ALEMBIC


def test_nc07_wrong_port_owner(mig):
    mig.world.alien_on_port = True
    assert dep(mig, "r_add", "--dry-run", tty=False) == rc.Exit.PREFLIGHT


def test_nc08_migration_without_a_backup(mig, monkeypatch):
    mig.record_rehearsal()
    def no_backup(**kw):
        raise rc.OpsError(rc.Exit.BACKUP, "backup unavailable")
    monkeypatch.setattr(ddb, "create_backup", no_backup)
    assert dep(mig, "r_add", "--allow-migration") == rc.Exit.BACKUP
    assert mig.world.db_revision == "bbb222" and not any("upgrade" in a for args in mig.world.argv_log for a in args)


def test_nc09_migration_whose_backup_does_not_restore(mig):
    mig.world.restore_drops_rows = True
    assert dep(mig, "r_add", "--allow-migration") == rc.Exit.BACKUP
    assert mig.world.db_revision == "bbb222" and mig.world.restarts == 0
    assert mig.world.scratch is None and mig.world.scratch_destroyed == mig.world.scratch_created == 1


def test_nc10_breaking_migration(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.activate("r1"); s.release("r_break", migration_files("ccc333", "bbb222", "breaking")); s.record_rehearsal()
    assert dep(s, "r_break", "--allow-migration") == rc.Exit.MIGRATION_NOT_AUTHORIZED and s.world.db_revision == "bbb222"


def test_nc11_failed_candidate(mig):
    assert dep(mig, "r_add", "--dry-run") == rc.Exit.MIGRATION_NOT_AUTHORIZED  # migration policy alone
    s = Scenario(mig.tmp / "s2"); s.release("r1"); s.release("r2"); s.activate("r1")
    assert dep(s, "r2", candidate_ok=False) == rc.Exit.SMOKE and s.current() == s.ids["r1"] and s.world.restarts == 0


def test_nc12_failed_restart_simulation_rolls_back_code_only_and_not_after_a_migration(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.release("r2"); s.activate("r1")
    real = ad.SystemdService  # noqa: F841
    calls = {"n": 0}
    ctx = s.ctx(answers=[s.ids["r2"]])
    good = ctx.service.restart
    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise rc.OpsError(rc.Exit.ACTIVATION_ROLLBACK_FAILED, "systemctl restart failed")
        good()
    ctx.service.restart = flaky
    assert ad.main(["--root", str(s.root), "--rehearsal", "--prod-port", "18000", "--candidate-port", "18001", "deploy", s.ids["r2"]], ctx) == rc.Exit.ACTIVATION_ROLLED_BACK
    assert s.current() == s.ids["r1"] and calls["n"] == 2
    m = Scenario(tmp_path / "m"); m.release("r1"); m.activate("r1"); m.release("r_add", migration_files("ccc333", "bbb222", "additive")); m.record_rehearsal()
    ctx = m.ctx(answers=[m.ids["r_add"]]); calls["n"] = 0
    good2 = ctx.service.restart
    def always_fails():
        calls["n"] += 1
        raise rc.OpsError(rc.Exit.ACTIVATION_ROLLBACK_FAILED, "systemctl restart failed")
    ctx.service.restart = always_fails
    assert ad.main(["--root", str(m.root), "--rehearsal", "--prod-port", "18000", "--candidate-port", "18001", "deploy", m.ids["r_add"], "--allow-migration"], ctx) == rc.Exit.ACTIVATION_NO_AUTO_ROLLBACK
    assert calls["n"] == 1                                         # exactly one attempt: no automatic second restart


def test_nc13_incompatible_rollback(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.release("r_break", migration_files("ccc333", "bbb222", "breaking")); s.activate("r_break", "r1")
    s.world.db_revision = "ccc333"
    assert s.run(["rollback", "--dry-run"], tty=False) == rc.Exit.ROLLBACK_INCOMPATIBLE


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "a\\b", "/abs", " 20260921T030000Z-aaaaaaaaaaaa", "20260921T030000Z-aaaaaaaaaaaa\n", "20260921T030000Z-AAAAAAAAAAAA",
                                 "20260921T030000-aaaaaaaaaaaa", "2026-09-21T03:00:00Z-aaaaaaaaaaaa", "20261399T030000Z-aaaaaaaaaaaa", "20260921T030000Z-aaaaaaaaaaaaa", "latest", None, 7])
def test_nc14_malformed_release_ids(bad):
    with pytest.raises(rc.OpsError) as err:
        rc.validate_release_id(bad)
    assert err.value.code == rc.Exit.ARTIFACT_INVALID


@pytest.mark.parametrize("name", ["../../etc/cron.d/x", "/tmp/x", "app/../../x", "app/./x"])
def test_nc15_archive_traversal(tmp_path, name):
    art = build(make_repo(tmp_path), tmp_path / "o")
    import gzip
    raw = io.BytesIO()
    with tarfile.open(art.artifact, "r:gz") as src, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as out:
        for m in src:
            out.addfile(m, src.extractfile(m))
        info = tarfile.TarInfo(name); info.size = 1; out.addfile(info, io.BytesIO(b"x"))
    art.artifact.write_bytes(raw.getvalue())
    art.sidecar.write_text(f"{rc.sha256_bytes(raw.getvalue())}  {art.artifact.name}\n")
    with pytest.raises(rc.OpsError):
        ra.verify_artifact(art.artifact)
    assert not (tmp_path / "etc").exists() and not __import__("os").path.exists("/tmp/x")
