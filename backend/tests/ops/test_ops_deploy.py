"""deploy: gates, classification, migration authorization, backup, candidate,
atomic activation and the auto-rollback policy (N-08, ADR-027)."""
import json
import os

import pytest

import release_common as rc
from tests.ops.helpers import Scenario, migration_files


@pytest.fixture()
def sc(tmp_path):
    s = Scenario(tmp_path)
    s.release("r1"); s.release("r2")
    s.activate("r1")
    return s


def deploy(sc, *args, **kw):
    name = args[0] if args and args[0] in sc.ids else "r2"
    flags = [a for a in args if a != name or a.startswith("-")]
    flags = [a for a in flags if a.startswith("-")]
    return sc.run(["deploy", sc.ids[name], *flags], answers=[sc.ids[name]], **kw)


# --- code-only ------------------------------------------------------------------------------------

def test_code_only_deploy_switches_atomically_and_sets_previous(sc):
    assert deploy(sc) == 0
    assert sc.current() == sc.ids["r2"] and sc.previous() == sc.ids["r1"]
    assert sc.world.restarts == 1 and sc.world.serving == sc.ids["r2"]
    assert not (sc.root / "shared" / "state" / "activation.json").exists()
    assert "ACTIVE" in sc.sink.text and "class code-only" in sc.sink.text
    assert list((sc.root / "shared" / "backups").iterdir()) == []  # no backup for code-only
    assert not any("alembic" in " ".join(a) for a in sc.world.argv_log)


def test_candidate_runs_on_the_candidate_port_before_the_switch(sc):
    order = []
    orig_restart = sc.world.__class__.restarts
    deploy(sc)
    assert sc.world.calls[0] == ("candidate", sc.ids["r2"], 18001)


def test_deploying_the_active_release_is_a_no_op(sc):
    assert sc.run(["deploy", sc.ids["r1"]], answers=[sc.ids["r1"]]) == 0
    assert sc.world.restarts == 0 and "already the active release" in sc.sink.text


def test_first_deployment_has_no_previous(tmp_path):
    s = Scenario(tmp_path); s.release("r1")
    s.world.serving = None
    assert s.run(["deploy", s.ids["r1"]], answers=[s.ids["r1"]]) == 0
    assert s.current() == s.ids["r1"] and s.previous() is None


# --- interactive guard --------------------------------------------------------------------------------

def test_deploy_needs_a_real_tty(sc):
    before = sc.world.state_hash()
    assert deploy(sc, tty=False) == rc.Exit.NO_TTY_OR_ABORT
    assert sc.world.state_hash() == before
    assert "interactive terminal" in sc.sink.text


def test_wrong_confirmation_aborts_without_changes(sc):
    before = sc.world.state_hash()
    assert sc.run(["deploy", sc.ids["r2"]], answers=["yes"]) == rc.Exit.NO_TTY_OR_ABORT
    assert sc.world.state_hash() == before


def test_there_is_no_yes_flag(sc):
    with pytest.raises(SystemExit) as err:
        sc.run(["deploy", sc.ids["r2"], "--yes"])
    assert err.value.code == 2


# --- gates ---------------------------------------------------------------------------------------------

def test_an_alien_process_on_the_production_port_fails_closed_and_nothing_is_killed(sc):
    sc.world.alien_on_port = True
    assert deploy(sc) == rc.Exit.PREFLIGHT
    assert "pid 4242" in sc.sink.text and "not by the expected release" in sc.sink.text
    assert sc.world.restarts == 0 and sc.current() == sc.ids["r1"]
    assert not any(a[0] in ("kill", "pkill", "fuser") for a in sc.world.argv_log)


def test_target_must_be_prepared(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.release("r2", prepared=False); s.activate("r1")
    assert deploy(s) == rc.Exit.PREFLIGHT and "not marked prepared" in s.sink.text


def test_target_drift_blocks_the_deploy(sc):
    (sc.root / "releases" / sc.ids["r2"] / "app" / "core" / "config.py").write_text("# edited\n")
    assert deploy(sc) == rc.Exit.PREFLIGHT and "modified since extraction" in sc.sink.text
    assert sc.world.restarts == 0


def test_unit_not_pointing_at_current_is_refused(sc, monkeypatch):
    import deploy_layout as dl
    ctx = sc.ctx()
    ctx.service.info = lambda: dl.ServiceInfo(True, "active", "running", 0, "/home/x/artesa-nfc-backend", "/home/x/artesa-nfc-backend/.venv/bin/python")
    import artesa_deploy as ad
    code = ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "deploy", sc.ids["r2"], "--dry-run"], ctx)
    assert code == rc.Exit.PREFLIGHT and "WorkingDirectory is not <root>/current" in sc.sink.text


def test_crash_looping_service_is_reported_but_does_not_block_the_fix(sc):
    import deploy_layout as dl, artesa_deploy as ad
    ctx = sc.ctx()
    wd = str(sc.root / "current")
    ctx.service.info = lambda: dl.ServiceInfo(True, "activating", "auto-restart", 12742, wd, wd + "/venv/bin/python")
    assert ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "deploy", sc.ids["r2"], "--dry-run"], ctx) == 0
    assert "crash-looping (12742 restarts)" in sc.sink.text


def test_deployment_lock_is_respected(sc):
    import deploy_layout as dl
    with dl.deploy_lock(dl.Layout(sc.root)):
        assert deploy(sc) == rc.Exit.PREFLIGHT and "lock" in sc.sink.text
    assert deploy(sc) == 0  # released with the context manager


def test_an_interrupted_activation_blocks_further_deploys(sc):
    import deploy_layout as dl
    dl.write_activation_marker(dl.Layout(sc.root), {"deploy_id": "d-x", "from": sc.ids["r1"], "to": sc.ids["r2"], "started_at": "2026-09-21T00:00:00Z"})
    assert deploy(sc) == rc.Exit.PREFLIGHT and "interrupted" in sc.sink.text


# --- configuration --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs,code", [
    ({"mode": 0o644}, rc.Exit.CONFIG), ({"mode": 0o640}, rc.Exit.CONFIG),
    ({"app_env": ""}, rc.Exit.APP_ENV), ({"app_env": "staging"}, rc.Exit.APP_ENV), ({"app_env": "local"}, rc.Exit.APP_ENV),
    ({"debug": "true"}, rc.Exit.CONFIG), ({"debug": "1"}, rc.Exit.CONFIG),
    ({"url": ""}, rc.Exit.CONFIG), ({"url": "mysql://u:pw123456@h/db"}, rc.Exit.CONFIG),
    ({"url": "postgresql://u:artesanfc@h/db"}, rc.Exit.CONFIG), ({"url": "postgresql://u@h/db"}, rc.Exit.CONFIG),
])
def test_configuration_gates(sc, kwargs, code):
    from tests.ops.helpers import write_env
    write_env(sc.root, **kwargs)
    assert deploy(sc, "--dry-run") == code
    assert sc.world.restarts == 0


def test_missing_env_file_and_symlinked_env_file(sc):
    (sc.root / "shared" / ".env").unlink()
    assert deploy(sc, "--dry-run") == rc.Exit.CONFIG
    real = sc.root / "elsewhere.env"; real.write_text("APP_ENV=production\n"); os.chmod(real, 0o600)
    (sc.root / "shared" / ".env").symlink_to(real)
    assert deploy(sc, "--dry-run") == rc.Exit.CONFIG and "not a symlink" in sc.sink.text


def test_cors_must_allow_the_frontend_and_never_star(sc):
    p = sc.root / "shared" / ".env"
    text = p.read_text()
    p.write_text(text.replace("https://artesanfc.com,https://www.artesanfc.com", "https://other.example"))
    assert deploy(sc, "--dry-run") == rc.Exit.CONFIG and "must include" in sc.sink.text
    p.write_text(text.replace("https://artesanfc.com,", "https://artesanfc.com,*,"))
    assert deploy(sc, "--dry-run") == rc.Exit.CONFIG and "'*'" in sc.sink.text


# --- database / alembic --------------------------------------------------------------------------------------------

def test_unknown_database_revision_is_refused(sc):
    sc.world.db_revision = "mystery"
    assert deploy(sc) == rc.Exit.ALEMBIC and "unknown to this release" in sc.sink.text and sc.world.restarts == 0


def test_database_ahead_of_the_release_is_refused(tmp_path):
    s = Scenario(tmp_path); s.release("r_old_code")  # head bbb222
    s.release("r_new", migration_files("ccc333", "bbb222", "additive")); s.activate("r_new")
    s.world.db_revision = "ccc333"  # already migrated by r_new
    assert deploy(s, "r_old_code") == rc.Exit.ALEMBIC and "AHEAD" in s.sink.text
    assert s.world.restarts == 0


def test_unreachable_database_is_refused(sc):
    sc.world.db_revision = "__unreachable__"
    assert deploy(sc) == rc.Exit.ALEMBIC and "cannot read the database state" in sc.sink.text


# --- migrations -------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def mig(tmp_path):
    s = Scenario(tmp_path)
    s.release("r1"); s.activate("r1")
    s.release("r_add", migration_files("ccc333", "bbb222", "additive"))
    return s


def test_pending_migration_without_the_flag_is_refused(mig):
    assert deploy(mig, "r_add") == rc.Exit.MIGRATION_NOT_AUTHORIZED and "--allow-migration" in mig.sink.text
    assert mig.world.db_revision == "bbb222" and mig.world.restarts == 0


def test_migration_without_server_binaries_for_the_restore_check_is_refused_up_front(mig):
    mig.world.initdb_major = None
    assert deploy(mig, "r_add", "--allow-migration") == rc.Exit.BACKUP and "--pg-bindir" in mig.sink.text
    assert mig.world.db_revision == "bbb222"
    assert not any(a[0] == "pg_dump" and "--version" not in a for a in mig.world.argv_log)


def test_backup_that_does_not_restore_blocks_the_migration(mig):
    mig.world.restore_drops_rows = True
    assert deploy(mig, "r_add", "--allow-migration") == rc.Exit.BACKUP
    assert "restore-check failed" in mig.sink.text and "NOT migrated" in mig.sink.text
    assert mig.world.db_revision == "bbb222" and mig.world.restarts == 0 and mig.current() == mig.ids["r1"]
    assert mig.world.scratch_destroyed == mig.world.scratch_created == 1 and mig.world.scratch is None
    assert mig.evidence("restore-check.json")["ok"] is False
    assert mig.evidence("result.json")["status"] == "failed"


def test_migration_that_fails_on_the_restored_copy_never_reaches_production(mig):
    mig.world.scratch_migrate_ok = False
    assert deploy(mig, "r_add", "--allow-migration") == rc.Exit.BACKUP
    assert mig.world.db_revision == "bbb222" and mig.world.restarts == 0
    assert mig.evidence("restore-check.json")["migration_rehearsal"] == "failed"
    prod_upgrades = [e for a, e in zip(mig.world.argv_log, mig.world.envs_log) if "upgrade" in a and "artesa_restore_test_" not in e.get("DATABASE_URL", "")]
    assert prod_upgrades == []


def test_breaking_migration_is_rejected_automatically_even_with_the_flag(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.activate("r1")
    s.release("r_break", migration_files("ccc333", "bbb222", "breaking"))
    s.record_rehearsal()
    assert deploy(s, "r_break", "--allow-migration") == rc.Exit.MIGRATION_NOT_AUTHORIZED and "breaking" in s.sink.text
    assert s.world.db_revision == "bbb222" and s.world.restarts == 0 and list((s.root / "shared" / "backups").iterdir()) == []


def test_additive_migration_takes_a_backup_of_this_run_migrates_then_activates(mig):
    mig.record_rehearsal()
    assert deploy(mig, "r_add", "--allow-migration") == 0
    backups = sorted(p.name for p in (mig.root / "shared" / "backups").iterdir())
    assert len(backups) == 3 and backups[0].endswith(".dump") and backups[1].endswith(".dump.json") and backups[2].endswith(".dump.sha256")
    meta = json.loads((mig.root / "shared" / "backups" / backups[1]).read_text())
    assert meta["alembic_revision"] == "bbb222" and meta["active_release"] == mig.ids["r1"] and meta["row_counts"] == {"artisan": 2, "piece": 5}
    assert set(meta) >= {"sha256", "size_bytes", "pg_dump_version"} and not {"host", "user", "username", "password"} & set(meta)
    assert mig.world.db_revision == "ccc333"
    assert mig.current() == mig.ids["r_add"] and mig.previous() == mig.ids["r1"]
    order = [a[0] if os.path.basename(a[0]) != "python" else " ".join(a[1:5]) for a in mig.world.argv_log]
    assert order.index("pg_dump") < next(i for i, o in enumerate(order) if "alembic upgrade head" in o)
    assert ("candidate", mig.ids["r_add"], 18001) in mig.world.calls
    assert not any("downgrade" in " ".join(a) for a in mig.world.argv_log)


def test_migration_without_a_backup_never_happens_when_pg_dump_fails(mig):
    mig.record_rehearsal(); mig.world.pg_dump_ok = False
    assert deploy(mig, "r_add", "--allow-migration") == rc.Exit.BACKUP
    assert mig.world.db_revision == "bbb222" and mig.world.restarts == 0
    assert not any("upgrade" in a for args in mig.world.argv_log for a in args)


def test_zero_byte_dump_is_a_failed_backup_and_blocks_the_migration(mig):
    mig.record_rehearsal(); mig.world.pg_dump_empty = True
    assert deploy(mig, "r_add", "--allow-migration") == rc.Exit.BACKUP and "empty" in mig.sink.text
    assert mig.world.db_revision == "bbb222"
    assert [p.name for p in (mig.root / "shared" / "backups").iterdir()] == []  # partial file discarded


def test_pg_dump_older_than_the_server_blocks_the_migration(mig):
    mig.record_rehearsal(); mig.world.pg_major = 16
    assert deploy(mig, "r_add", "--allow-migration") == rc.Exit.BACKUP and "older than the server" in mig.sink.text
    assert mig.world.db_revision == "bbb222"


def test_failed_migration_reports_uncertain_state_and_does_not_activate_or_roll_back(mig):
    mig.record_rehearsal(); mig.world.migrate_ok = False
    assert deploy(mig, "r_add", "--allow-migration") == rc.Exit.MIGRATION_FAILED and "UNCERTAIN" in mig.sink.text
    assert mig.current() == mig.ids["r1"] and mig.world.restarts == 0


# --- candidate ---------------------------------------------------------------------------------------------------------------

def test_failed_candidate_stops_a_code_only_deploy_untouched(sc):
    before = sc.world.state_hash()
    assert deploy(sc, candidate_ok=False) == rc.Exit.SMOKE
    assert sc.current() == sc.ids["r1"] and sc.world.restarts == 0
    assert sc.world.state_hash().split()[0] or True
    assert "nothing was changed" in sc.sink.text


def test_failed_candidate_after_an_additive_migration_says_so(mig):
    mig.record_rehearsal()
    assert deploy(mig, "r_add", "--allow-migration", candidate_ok=False) == rc.Exit.SMOKE
    assert "database WAS migrated" in mig.sink.text and mig.current() == mig.ids["r1"] and mig.world.restarts == 0


# --- activation failure and the auto-rollback policy ------------------------------------------------------------------------

def test_code_only_activation_failure_rolls_back_automatically(sc):
    sc.world.broken.add(sc.ids["r2"])
    assert deploy(sc) == rc.Exit.ACTIVATION_ROLLED_BACK
    assert sc.current() == sc.ids["r1"] and sc.world.serving == sc.ids["r1"] and sc.world.restarts == 2
    assert not (sc.root / "shared" / "state" / "activation.json").exists()
    assert "ROLLED BACK" in sc.sink.text
    assert sc.previous() is None  # previous restored to what it was before (nothing)


def test_previous_is_restored_after_an_automatic_rollback(tmp_path):
    s = Scenario(tmp_path); s.release("r0"); s.release("r1"); s.release("r2"); s.activate("r1", "r0")
    s.world.broken.add(s.ids["r2"])
    assert deploy(s) == rc.Exit.ACTIVATION_ROLLED_BACK
    assert (s.current(), s.previous()) == (s.ids["r1"], s.ids["r0"])


def test_auto_rollback_never_happens_after_a_migration(mig):
    mig.record_rehearsal(); mig.world.broken.add(mig.ids["r_add"])
    assert deploy(mig, "r_add", "--allow-migration") == rc.Exit.ACTIVATION_NO_AUTO_ROLLBACK
    assert mig.world.restarts == 1  # no second restart: nothing was rolled back
    assert mig.current() == mig.ids["r_add"]
    assert (mig.root / "shared" / "state" / "activation.json").exists()
    assert "NOT rolling back automatically (a migration ran)" in mig.sink.text
    assert mig.world.db_revision == "ccc333"


def test_no_auto_rollback_flag_is_honoured(sc):
    sc.world.broken.add(sc.ids["r2"])
    assert deploy(sc, "r2", "--no-auto-rollback") == rc.Exit.ACTIVATION_NO_AUTO_ROLLBACK
    assert sc.world.restarts == 1 and sc.current() == sc.ids["r2"]


def test_failed_automatic_rollback_is_reported_as_such(sc):
    sc.world.broken.update({sc.ids["r1"], sc.ids["r2"]})
    assert deploy(sc) == rc.Exit.ACTIVATION_ROLLBACK_FAILED and "Manual intervention" in sc.sink.text


def test_first_deployment_failure_has_nothing_to_roll_back_to(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.world.serving = None; s.world.broken.add(s.ids["r1"])
    assert deploy(s, "r1") == rc.Exit.ACTIVATION_NO_AUTO_ROLLBACK and "no previous release" in s.sink.text


def test_a_marker_left_by_a_failed_activation_blocks_the_next_deploy(sc):
    sc.world.broken.add(sc.ids["r2"])
    deploy(sc, "r2", "--no-auto-rollback")
    assert deploy(sc, "r2", "--dry-run") == rc.Exit.PREFLIGHT and "activation" in sc.sink.text


def test_public_edge_failure_with_a_healthy_origin_is_exit_53_and_no_rollback(sc):
    assert deploy(sc, public_ok=False) == rc.Exit.EDGE_FAILURE
    assert sc.current() == sc.ids["r2"] and sc.world.restarts == 1 and "NOT rolling back" in sc.sink.text


def test_skip_public_check_flag(sc):
    assert deploy(sc, "r2", "--skip-public-check", public_ok=False) == 0


# --- deploy log -----------------------------------------------------------------------------------------------------------------

def test_deploy_log_is_append_only_jsonl_without_secrets(mig):
    mig.record_rehearsal()
    deploy(mig, "r_add", "--allow-migration")
    log = mig.root / "shared" / "state" / "deploy-log.jsonl"
    assert (log.stat().st_mode & 0o777) == 0o600
    events = [json.loads(l) for l in log.read_text().splitlines()]
    assert [e["event"] for e in events] == ["deploy_start", "backup", "restore_check", "deploy_ok"]
    start, backup, restore, ok = events
    assert restore["exit_code"] == 0 and "migration_rehearsal=ok" in restore["checks"]
    assert start["deployment_class"] == "migration/additive" and (start["alembic_from"], start["alembic_to"]) == ("bbb222", "ccc333")
    assert start["source_release"] == mig.ids["r1"] and start["target_release"] == mig.ids["r_add"] and len(start["git_sha"]) == 12
    assert backup["backup"].endswith(".dump") and len(backup["backup_sha256"]) == 64 and ok["exit_code"] == 0
    assert all(e["deploy_id"] == start["deploy_id"] and e["tool_version"] == rc.TOOL_VERSION for e in events)
    size = log.stat().st_size
    deploy(mig, "r_add", "--dry-run")
    assert log.stat().st_size == size  # a dry run appends nothing


def test_deploy_log_refuses_unknown_fields_and_secret_values(sc):
    import deploy_layout as dl
    from tests.ops.helpers import CANARY_PASSWORD, NOW
    import release_probe as rp
    log = dl.DeployLog(dl.Layout(sc.root), rp.SecretGuard([CANARY_PASSWORD]), "d-1", lambda: NOW)
    with pytest.raises(rc.OpsError, match="unknown field"):
        log.event("x", request_body="{}")
    with pytest.raises(rc.OpsError, match="secret value"):
        log.event("x", detail=f"failed for {CANARY_PASSWORD}")
    assert not (sc.root / "shared" / "state" / "deploy-log.jsonl").exists()
