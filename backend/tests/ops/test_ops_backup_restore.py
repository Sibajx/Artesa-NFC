"""PostgreSQL backup and restore rehearsal (N-08, ADR-027). pg_dump/pg_restore
are faked here; tests/ops/rehearsal runs the real thing against PostgreSQL 18."""
import json
import os
import stat
from datetime import timedelta

import pytest

import deploy_db as ddb
import deploy_layout as dl
import release_common as rc
import release_probe as rp
from tests.ops.helpers import CANARY_OTHER, CANARY_PASSWORD, CANARY_USER, DATABASE_URL, NOW, FakeRunner, Scenario, World

SCRATCH = "postgresql://scratch_user:Scratch-pw-123@127.0.0.1:55499/artesa_restore_test"


@pytest.fixture()
def env(tmp_path):
    sc = Scenario(tmp_path)
    runner = FakeRunner(sc.world)
    envfile = rp.read_env_file(sc.root / "shared" / ".env"); runner.guard.merge(envfile.guard)
    return sc, runner, envfile, dl.Layout(sc.root)


def state(sc) -> ddb.DbState:
    return ddb.DbState(sc.world.server_version_num, [sc.world.db_revision], dict(sc.world.row_counts))


def backup(env):
    sc, runner, envfile, layout = env
    return ddb.create_backup(runner=runner, layout=layout, env_file=envfile, state=state(sc), active_release="rel-1", clock=lambda: NOW)


def test_backup_uses_pg_env_not_argv_and_writes_0600_files_with_a_clean_sidecar(env):
    sc, runner, *_ = env
    result = backup(env)
    dump_call = next(a for a in sc.world.argv_log if a[0] == "pg_dump" and "--version" not in a)
    assert dump_call[:2] == ["pg_dump", "-Fc"] and not any(CANARY_PASSWORD in x or CANARY_USER in x or "127.0.0.1" in x for x in dump_call)
    envs = [e for a, e in zip(sc.world.argv_log, sc.world.envs_log) if a is dump_call]
    assert envs[0]["PGPASSWORD"] == CANARY_PASSWORD and envs[0]["PGHOST"] == "127.0.0.1" and envs[0]["PGDATABASE"] == "artesa_prod"
    assert stat.S_IMODE(result.path.stat().st_mode) == 0o600 and stat.S_IMODE(result.sidecar.stat().st_mode) == 0o600
    meta = json.loads(result.sidecar.read_text())
    assert meta["sha256"] == rc.sha256_file(str(result.path)) == result.sha256
    assert meta["size_bytes"] == result.path.stat().st_size and meta["alembic_revision"] == "bbb222" and meta["server_major"] == 18
    assert meta["active_release"] == "rel-1" and meta["row_counts"] == {"artisan": 2, "piece": 5}
    text = result.sidecar.read_text()
    for forbidden in (CANARY_PASSWORD, CANARY_USER, "127.0.0.1", "5433", "artesa_prod"):
        assert forbidden not in text
    assert not list(result.path.parent.glob(".*partial"))


def test_backup_file_name_carries_utc_time_and_revision(env):
    assert backup(env).path.name == "artesa-nfc-20260921T030000Z-bbb222.dump"


def test_pg_dump_failure_leaves_nothing_and_scrubs_the_error(env):
    sc, runner, envfile, layout = env
    sc.world.pg_dump_ok = False
    with pytest.raises(rc.OpsError) as err:
        backup(env)
    assert err.value.code == rc.Exit.BACKUP and list(layout.backups.iterdir()) == []


def test_zero_byte_dump_is_refused(env):
    env[0].world.pg_dump_empty = True
    with pytest.raises(rc.OpsError, match="empty") as err:
        backup(env)
    assert err.value.code == rc.Exit.BACKUP and list(env[3].backups.iterdir()) == []


def test_invalid_toc_is_refused(env):
    sc, runner, *_ = env
    runner.handlers = [lambda argv, e, c: rp.RunResult(1, "", "not an archive") if argv[0] == "pg_restore" else None]
    with pytest.raises(rc.OpsError, match="readable PostgreSQL") as err:
        backup(env)
    assert err.value.code == rc.Exit.BACKUP and list(env[3].backups.iterdir()) == []


def test_dump_must_contain_alembic_version_and_every_live_table(env):
    sc, runner, *_ = env
    def toc(argv, e, c):
        if argv[0] == "pg_restore" and "--list" in argv:
            return rp.RunResult(0, "1; 1259 1 TABLE public alembic_version o\n2; 1259 2 TABLE public artisan o\n")  # piece missing
    runner.handlers = [toc]
    with pytest.raises(rc.OpsError, match="missing table.*piece"):
        backup(env)


def test_pg_dump_older_than_the_server_is_refused(env):
    env[0].world.pg_major = 17
    with pytest.raises(rc.OpsError, match="older than the server"):
        backup(env)


def test_pg_dump_missing_is_refused(env):
    sc, runner, *_ = env
    runner.handlers = [lambda argv, e, c: rp.RunResult(127, "", "command not found") if argv[0] == "pg_dump" else None]
    with pytest.raises(rc.OpsError, match="not available"):
        backup(env)


def test_verify_dump_checksum_and_missing_file(env):
    sc, runner, envfile, layout = env
    result = backup(env)
    assert ddb.verify_dump(runner, result.path, set(sc.world.row_counts), expect_sha=result.sha256) == result.sha256
    with pytest.raises(rc.OpsError, match="recorded checksum"):
        ddb.verify_dump(runner, result.path, set(), expect_sha="0" * 64)
    with pytest.raises(rc.OpsError, match="not found"):
        ddb.verify_dump(runner, layout.backups / "nope.dump", set())


def test_a_second_backup_in_the_same_second_does_not_overwrite(env):
    backup(env)
    with pytest.raises(rc.OpsError, match="already exists"):
        backup(env)


# --- restore-check ----------------------------------------------------------------------------

def probe_dir(sc):
    d = sc.root / "releases" / "probe"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ephemeral(env):
    sc, runner, envfile, layout = env
    return ddb.EphemeralCluster(runner=runner, bindir=sc.tmp / "pgbin", parent=layout.restore_tmp, min_major=18)


def check(env, target=None, **kw):
    sc, runner, envfile, layout = env
    result = backup(env)
    return ddb.restore_check(runner=runner, dump=result.path, target=target or ephemeral(env), probe_release_dir=probe_dir(sc),
                             app_env=envfile.values, **kw)


def test_ephemeral_restore_check_creates_restores_compares_and_destroys(env):
    sc, runner, envfile, layout = env
    report = check(env)
    assert report.ok and report.counts_match and report.problems == [] and report.cleanup_ok
    assert report.target_kind == "ephemeral-cluster" and report.tables == 2 and report.alembic_revision == "bbb222"
    names = [os.path.basename(a[0]) + ("-list" if "--list" in a else "") for a in sc.world.argv_log]
    assert names.index("initdb") < names.index("createdb") < names.index("pg_restore") < len(names) - 1
    stop = [a for a in sc.world.argv_log if os.path.basename(a[0]) == "pg_ctl" and a[-1] == "stop"]
    assert stop, "the disposable cluster must be stopped"
    assert sc.world.scratch_created == 1
    assert not layout.restore_tmp.exists() or list(layout.restore_tmp.iterdir()) == []   # temp dir destroyed
    # the throw-away cluster is socket-only and never uses a production connection
    start = next(a for a in sc.world.argv_log if os.path.basename(a[0]) == "pg_ctl" and a[-1] == "start")
    assert "listen_addresses=''" in start[start.index("-o") + 1]
    restore_env = next(e for a, e in zip(sc.world.argv_log, sc.world.envs_log) if os.path.basename(a[0]) == "pg_restore" and "--list" not in a)
    sock = restore_env["PGHOST"]
    assert os.path.basename(sock).startswith("artesa-rc-") and len(sock) < 90 and not os.path.exists(sock)   # short, private, removed
    assert restore_env.get("PGPASSWORD") and restore_env["PGPASSWORD"] != CANARY_PASSWORD
    assert CANARY_PASSWORD not in json.dumps(sc.world.envs_log[names.index("pg_restore") :])


def test_one_off_cluster_password_never_reaches_argv_or_the_report(env):
    sc, runner, *_ = env
    report = check(env)
    pw = next(e["PGPASSWORD"] for a, e in zip(sc.world.argv_log, sc.world.envs_log) if os.path.basename(a[0]) == "createdb")
    assert runner.guard.contains(pw)
    assert not any(pw in x for a in sc.world.argv_log for x in a)
    assert pw not in json.dumps(report.evidence())


def test_restore_check_reports_a_row_count_mismatch(env):
    env[0].world.restore_drops_rows = True
    report = check(env)
    assert not report.ok and not report.counts_match and "row counts differ" in report.problems[0]
    assert report.cleanup_ok and env[0].world.scratch_created == 1


def test_restore_check_reports_a_wrong_alembic_revision(env):
    sc = env[0]
    result = backup(env)
    meta = json.loads(result.sidecar.read_text()); meta["alembic_revision"] = "aaa111"
    os.chmod(result.sidecar, 0o600); result.sidecar.write_text(json.dumps(meta))
    report = ddb.restore_check(runner=env[1], dump=result.path, target=ephemeral(env), probe_release_dir=probe_dir(sc))
    assert not report.ok and any("Alembic revision" in p for p in report.problems)


def test_restore_check_rehearses_the_target_migration_on_the_copy_only(env):
    sc = env[0]
    s2 = sc.release("r_add", {"backend/alembic/versions/ccc333_new.py": "revision = 'ccc333'\ndown_revision = 'bbb222'\n",
                              "backend/ops/migration-classes.json": json.dumps({"schema_version": 1, "revisions": {
                                  "aaa111": {"class": "baseline", "note": "x"}, "bbb222": {"class": "additive", "note": "x"},
                                  "ccc333": {"class": "additive", "note": "x"}}})})
    report = check(env, migrate_release_dir=sc.root / "releases" / s2, expect_head="ccc333")
    assert report.ok and report.migration_rehearsal == "ok"
    assert sc.world.db_revision == "bbb222"          # production untouched
    upgrade_env = next(e for a, e in zip(sc.world.argv_log, sc.world.envs_log) if "upgrade" in a)
    assert "artesa_restore_test_" in upgrade_env["DATABASE_URL"] and CANARY_PASSWORD not in upgrade_env["DATABASE_URL"]


def test_failed_migration_rehearsal_fails_the_check_and_still_destroys(env):
    sc = env[0]
    sc.world.scratch_migrate_ok = False
    rid = sc.release("r2")
    report = check(env, migrate_release_dir=sc.root / "releases" / rid, expect_head="bbb222")
    assert not report.ok and report.migration_rehearsal == "failed" and report.cleanup_ok
    assert sc.world.db_revision == "bbb222"


def test_initdb_older_than_the_server_is_refused(env):
    env[0].world.initdb_major = 16
    with pytest.raises(rc.OpsError, match="older than the production server"):
        check(env)
    assert env[0].world.scratch_created == 0


def test_pg_restore_failure_still_destroys_the_disposable_cluster(env):
    sc = env[0]
    sc.world.restore_ok = False
    with pytest.raises(rc.OpsError, match="pg_restore failed"):
        check(env)
    assert any(os.path.basename(a[0]) == "pg_ctl" and a[-1] == "stop" for a in sc.world.argv_log)
    assert list(dl.Layout(sc.root).restore_tmp.iterdir()) == []


def test_restore_refuses_a_scratch_database_that_is_not_empty(env):
    sc, runner, *_ = env
    runner.handlers = [lambda a, e, c: rp.RunResult(0, json.dumps({"server_version_num": 180006, "alembic_versions": [], "row_counts": {"x": 1}}))
                       if a[-1] == "db-state" and "artesa_restore_test_" in e.get("DATABASE_URL", "") else None]
    with pytest.raises(rc.OpsError, match="not empty"):
        check(env)
    assert not any(os.path.basename(a[0]) == "pg_restore" and "--list" not in a for a in sc.world.argv_log)


def test_restore_refuses_a_dump_that_no_longer_matches_its_sidecar(env):
    sc, runner, *_ = env
    result = backup(env)
    os.chmod(result.path, 0o600); result.path.write_bytes(b"PGDMP-tampered")
    with pytest.raises(rc.OpsError, match="checksum"):
        ddb.restore_check(runner=runner, dump=result.path, target=ephemeral(env), probe_release_dir=probe_dir(sc))
    assert sc.world.scratch_created == 0


# --- scratch server (laptop / CI container) -----------------------------------------------------

def test_scratch_server_creates_a_unique_database_and_drops_it(env):
    sc, runner, envfile, layout = env
    target = ddb.ScratchServer(runner=runner, admin_url=SCRATCH, production_url=DATABASE_URL)
    report = check(env, target=target)
    assert report.ok and report.target_kind == "scratch-server"
    create = next(a for a in sc.world.argv_log if os.path.basename(a[0]) == "createdb")
    drop = next(a for a in sc.world.argv_log if os.path.basename(a[0]) == "dropdb")
    assert create[-1].startswith("artesa_restore_test_") and drop[-1] == create[-1] and "--if-exists" in drop
    assert not any("Scratch-pw-123" in x for a in sc.world.argv_log for x in a)
    assert sc.world.scratch_destroyed == 1


@pytest.mark.parametrize("url,match", [
    ("postgresql://u:pw123456@db.example.com:5432/postgres", "loopback"),
    ("postgresql://u:pw123456@127.0.0.1:5433/postgres", "production PostgreSQL"),
    ("postgresql://u:pw123456@localhost:5433/other", "production PostgreSQL"),
])
def test_scratch_server_is_never_remote_or_the_production_cluster(env, url, match):
    sc, runner, *_ = env
    target = ddb.ScratchServer(runner=runner, admin_url=url, production_url=DATABASE_URL)
    with pytest.raises(rc.OpsError, match=match):
        check(env, target=target)
    assert sc.world.scratch_created == 0 and not any(os.path.basename(a[0]) == "pg_restore" and "--list" not in a for a in sc.world.argv_log)


def test_backup_carries_commit_target_and_a_sha256sum_sidecar(env):
    sc, runner, envfile, layout = env
    result = ddb.create_backup(runner=runner, layout=layout, env_file=envfile, state=state(sc), active_release="rel-1", clock=lambda: NOW,
                               active_commit="0123456789abcdef" * 2 + "01234567", target_release="rel-2", deploy_id="d-1")
    assert result.path.name == "artesa-nfc-20260921T030000Z-bbb222-0123456789ab.dump"
    meta = json.loads(result.sidecar.read_text())
    assert meta["active_commit"].startswith("0123456789ab") and meta["target_release"] == "rel-2" and meta["deploy_id"] == "d-1"
    sha = result.path.with_name(result.path.name + ".sha256")
    assert sha.read_text() == f"{result.sha256}  {result.path.name}\n" and stat.S_IMODE(sha.stat().st_mode) == 0o600


def test_standalone_rehearsal_record_is_evidence_only(env):
    layout = env[3]
    ddb.record_rehearsal(layout, lambda: NOW, ddb.RestoreReport("f" * 64, "bbb222", True, [], "ephemeral-cluster"), "failed")
    entry = json.loads(layout.rehearsal_log.read_text().splitlines()[-1])
    assert entry["ok"] is False and entry["candidate"] == "failed" and entry["target"] == "ephemeral-cluster"
    assert stat.S_IMODE(layout.rehearsal_log.stat().st_mode) == 0o600


# --- artesa-deploy backup (on demand) records the active commit (#123) ------------------------------------

def cli_backups(s):
    dumps = sorted((s.root / "shared" / "backups").glob("*.dump")) if (s.root / "shared" / "backups").exists() else []
    return [(d, json.loads(d.with_name(d.name + ".json").read_text())) for d in dumps]


def test_on_demand_backup_records_the_active_commit(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.activate("r1")
    commit = json.loads((s.root / "releases" / s.ids["r1"] / "RELEASE.json").read_text())["git"]["commit"]
    assert s.run(["backup"]) == 0
    assert "[WARN]" not in s.sink.text
    [(dump, meta)] = cli_backups(s)
    assert meta["active_release"] == s.ids["r1"] and meta["active_commit"] == commit and len(commit) == 40
    assert dump.name.endswith(f"-{commit[:12]}.dump") and ddb.read_backup_meta(dump)["dump"] == dump.name
    [event] = s.log_events()
    assert event["event"] == "backup" and event["backup"] == dump.name and "detail" not in event
    assert s.run(["restore-check", str(dump), "--ephemeral"], tty=False) == 0 and "restore-check OK" in s.sink.text


@pytest.mark.parametrize("damage", ["missing", "not-json", "commit-mismatch"])
def test_on_demand_backup_warns_but_continues_when_the_active_commit_is_unknown(tmp_path, damage):
    # a backup matters most when something is already broken: warn, never block
    s = Scenario(tmp_path); s.release("r1"); s.activate("r1")
    release_json = s.root / "releases" / s.ids["r1"] / "RELEASE.json"
    if damage == "missing":
        release_json.unlink()
    elif damage == "not-json":
        release_json.write_text("{not json")
    else:  # well-formed but untrusted: git.commit no longer matches the release id
        data = json.loads(release_json.read_text()); data["git"]["commit"] = "f" * 40
        release_json.write_text(json.dumps(data))
    assert s.run(["backup"]) == 0
    assert f"[WARN] active release commit: cannot determine the commit of the active release {s.ids['r1']}" in s.sink.text
    assert "the backup continues with active_commit unset" in s.sink.text
    [(dump, meta)] = cli_backups(s)
    assert meta["active_release"] == s.ids["r1"] and meta["active_commit"] is None
    assert dump.name == "artesa-nfc-20260921T030000Z-bbb222.dump" and oct(dump.stat().st_mode & 0o777) == "0o600"
    [event] = s.log_events()
    assert event["event"] == "backup" and event["exit_code"] == 0 and event["detail"] == "active commit unknown"
    assert s.run(["restore-check", str(dump), "--ephemeral"], tty=False) == 0 and "restore-check OK" in s.sink.text


def test_on_demand_backup_without_an_active_release_is_refused_and_writes_nothing(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.world.serving = None
    assert s.run(["backup"]) == rc.Exit.PREFLIGHT and "no current release" in s.sink.text
    backups = s.root / "shared" / "backups"
    assert not backups.exists() or list(backups.iterdir()) == []
    assert s.log_events() == [] and not any(os.path.basename(a[0]) == "pg_dump" and "--version" not in a for a in s.world.argv_log)
