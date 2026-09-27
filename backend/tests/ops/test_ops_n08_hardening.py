"""N-08 implementation phase: commit authorization, artifact re-verification at
deploy time, dependency drift, reserved ports, per-deployment evidence, the
manual privileged step, retention, interrupted activations, the shared/.env
wrapper and the bin/ tool installation (ADR-027)."""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

import artesa_deploy as ad
import deploy_layout as dl
import release_common as rc
from tests.ops.helpers import CANARY_OTHER, CANARY_PASSWORD, CANARY_USER, DATABASE_URL, FakeService, Scenario, migration_files


@pytest.fixture()
def sc(tmp_path):
    s = Scenario(tmp_path)
    s.release("r1"); s.release("r2")
    s.activate("r1")
    return s


@pytest.fixture()
def mig(tmp_path):
    s = Scenario(tmp_path)
    s.release("r1"); s.activate("r1")
    s.release("r_add", migration_files("ccc333", "bbb222", "additive"))
    return s


def deploy(s, name="r2", *flags, answers=None, **kw):
    return s.run(["deploy", s.ids[name], *flags], answers=answers or [s.ids[name]], **kw)


def everything_under(root: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in root.rglob("*") if p.is_file() and "venv" not in p.parts)


# --- commit authorization (step 4) -------------------------------------------------------------------------------

def test_expect_commit_must_match_the_release_commit(sc):
    commit = json.loads((sc.root / "releases" / sc.ids["r2"] / "RELEASE.json").read_text())["git"]["commit"]
    assert deploy(sc, "r2", "--expect-commit", commit[:12]) == 0
    assert sc.current() == sc.ids["r2"]


def test_expect_commit_mismatch_is_refused_before_anything_changes(sc):
    before = sc.world.state_hash()
    assert deploy(sc, "r2", "--expect-commit", "0" * 40) == rc.Exit.ARTIFACT_INVALID
    assert "is not the expected commit" in sc.sink.text
    assert sc.world.state_hash() == before and sc.world.restarts == 0


@pytest.mark.parametrize("bad", ["abc", "ABCDEF123456", "1234567890ab; rm", "g" * 12])
def test_expect_commit_format_is_strict(sc, bad):
    assert deploy(sc, "r2", "--expect-commit", bad, "--dry-run") == rc.Exit.USAGE


def test_production_root_requires_expect_commit(sc):
    tool = ad.Tool(ad.Context(root=sc.root, rehearsal=False, runner=sc.ctx().runner, service=FakeService(sc.world)))
    release = json.loads((sc.root / "releases" / sc.ids["r2"] / "RELEASE.json").read_text())
    release = {**release, "channel": rc.CHANNEL_PRODUCTION}
    with pytest.raises(rc.OpsError, match="--expect-commit"):
        tool.commit_gate(release, None)
    assert "== --expect-commit" in tool.commit_gate(release, release["git"]["commit"])


def test_rehearsal_artifact_is_never_authorized_on_the_production_root(sc):
    tool = ad.Tool(ad.Context(root=sc.root, rehearsal=False, runner=sc.ctx().runner, service=FakeService(sc.world)))
    release = json.loads((sc.root / "releases" / sc.ids["r2"] / "RELEASE.json").read_text())
    with pytest.raises(rc.OpsError, match="rehearsal artifact"):
        tool.commit_gate(release, release["git"]["commit"])


# --- artifact re-verification + dependency drift (steps 1-3, 9-10) ---------------------------------------------------

def test_incoming_artifact_is_reverified_at_deploy_time(sc):
    artifact = sc.artifacts["r2"]
    data = bytearray(artifact.read_bytes()); data[-20] ^= 0xFF
    artifact.write_bytes(bytes(data))
    assert deploy(sc) == rc.Exit.ARTIFACT_INVALID and "checksum does not match" in sc.sink.text
    assert sc.world.restarts == 0 and sc.current() == sc.ids["r1"]


def test_missing_incoming_artifact_blocks_the_deploy(sc):
    sc.artifacts["r2"].unlink()
    assert deploy(sc) == rc.Exit.ARTIFACT_INVALID and sc.world.restarts == 0


def test_artifact_swapped_after_prepare_is_refused(sc):
    """Same name, valid sidecar, but not the bytes the release was prepared from."""
    from tests.ops.helpers import rewrite_tar
    rewrite_tar(sc.artifacts["r2"], lambda m: None)  # re-packed: different archive bytes, valid sidecar
    assert deploy(sc) == rc.Exit.ARTIFACT_INVALID and "not the one this release was prepared from" in sc.sink.text


def test_venv_drift_from_the_lock_blocks_the_deploy(sc):
    sc.world.extra_installed = {"requests": "2.32.0"}
    assert deploy(sc, "r2", "--dry-run") == rc.Exit.PREFLIGHT and "differs from the lock" in sc.sink.text
    sc.world.extra_installed = {"sqlalchemy": "2.0.99"}
    assert deploy(sc) == rc.Exit.PREFLIGHT and sc.world.restarts == 0


@pytest.mark.parametrize("port", [8000, 8002])
def test_candidate_never_binds_production_or_finanzas_ports(sc, port):
    ctx = sc.ctx(answers=[sc.ids["r2"]]); ctx.candidate_port = port
    code = ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "deploy", sc.ids["r2"]], ctx)
    assert code == rc.Exit.USAGE and "reserved" in sc.sink.text and sc.world.restarts == 0
    ctx = sc.ctx()
    assert ad.main(["--root", str(sc.root), "--rehearsal", "candidate", sc.ids["r2"], "--port", str(port)], ctx) == rc.Exit.USAGE


# --- evidence (phase 9) -------------------------------------------------------------------------------------------------

def test_code_only_deploy_leaves_complete_evidence_without_secrets(sc):
    assert deploy(sc) == 0
    [directory] = sc.evidence_dirs()
    commit = json.loads((sc.root / "releases" / sc.ids["r2"] / "RELEASE.json").read_text())["git"]["commit"]
    assert directory.name == f"20260921T030000Z-{commit[:12]}"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    names = sorted(p.name for p in directory.iterdir())
    assert names == ["alembic.json", "artifact.json", "candidate-smoke.json", "dependencies.json", "final-smoke.json",
                     "preflight.json", "release.json", "result.json", "retention.json"]
    assert all(stat.S_IMODE((directory / n).stat().st_mode) == 0o600 for n in names)
    result = sc.evidence("result.json")
    assert result["status"] == "ok" and result["exit_code"] == 0 and result["deploy_type"] == "CODE_ONLY"
    assert (result["current_after"], result["previous_after"]) == (sc.ids["r2"], sc.ids["r1"])
    assert sc.evidence("alembic.json") == {"before": "bbb222", "target": "bbb222", "after": "bbb222", "pending": [],
                                           "deployment_class": "code-only", "deploy_type": "CODE_ONLY"}
    art = sc.evidence("artifact.json")
    assert art["archive_sha256"] == sc.artifacts["r2"].with_name(sc.artifacts["r2"].name + ".sha256").read_text().split()[0]
    assert sc.evidence("release.json")["git"]["commit"] == commit
    assert sc.evidence("candidate-smoke.json")["ok"] and sc.evidence("final-smoke.json")["ok"]
    text = everything_under(directory)
    for secret in (CANARY_PASSWORD, CANARY_USER, DATABASE_URL, CANARY_OTHER, "another-secret-value-123"):
        assert secret not in text


def test_migration_deploy_evidence_records_backup_restore_check_and_revisions(mig):
    assert deploy(mig, "r_add", "--allow-migration") == 0
    alembic = mig.evidence("alembic.json")
    assert (alembic["before"], alembic["target"], alembic["after"]) == ("bbb222", "ccc333", "ccc333")
    assert alembic["deploy_type"] == "MIGRATION_DEPLOY" and alembic["pending"] == ["ccc333"]
    backup = mig.evidence("backup.json")
    assert len(backup["sha256"]) == 64 and backup["dump"].endswith(".dump") and backup["alembic_revision"] == "bbb222"
    restore = mig.evidence("restore-check.json")
    assert restore["ok"] and restore["target"] == "ephemeral-cluster" and restore["target_destroyed"] and restore["migration_rehearsal"] == "ok"
    assert restore["dump_sha256"] == backup["sha256"]
    assert mig.evidence("result.json")["status"] == "ok"
    assert CANARY_PASSWORD not in everything_under(mig.evidence_dirs()[-1])


def test_rolled_back_code_only_deploy_records_the_rollback(sc):
    sc.world.broken.add(sc.ids["r2"])
    assert deploy(sc) == rc.Exit.ACTIVATION_ROLLED_BACK
    rb = sc.evidence("rollback.json")
    assert rb["attempted"] and rb["automatic"] and rb["ok"] and rb["to"] == sc.ids["r1"]
    assert sc.evidence("final-smoke.json")["ok"] is False
    assert sc.evidence("result.json")["status"] == "rolled-back"


def test_migration_deploy_records_that_no_automatic_rollback_happened(mig):
    mig.world.broken.add(mig.ids["r_add"])
    assert deploy(mig, "r_add", "--allow-migration") == rc.Exit.ACTIVATION_NO_AUTO_ROLLBACK
    rb = mig.evidence("rollback.json")
    assert rb == {"attempted": False, "reason": "a migration ran", "policy": "automatic rollback only for CODE_ONLY deployments"}
    assert mig.evidence("result.json")["status"] == "failed-no-auto-rollback"
    assert mig.world.db_revision == "ccc333"  # no downgrade, ever


def test_dry_run_writes_no_evidence(sc):
    before = sc.world.state_hash()
    assert deploy(sc, "r2", "--dry-run") == 0
    assert sc.evidence_dirs() == [] and sc.world.state_hash() == before


def test_manual_rollback_leaves_its_own_evidence(sc):
    assert deploy(sc) == 0
    assert sc.run(["rollback"], answers=[sc.ids["r1"]]) == 0
    [rb_dir] = [d for d in sc.evidence_dirs() if d.name.endswith("-rollback")]
    result = json.loads((rb_dir / "result.json").read_text())
    assert result["kind"] == "manual-rollback" and result["status"] == "ok" and result["downgrade"] is False


def test_evidence_refuses_secret_values():
    import release_probe as rp
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        ev = dl.Evidence(Path(tmp), rp.SecretGuard([CANARY_PASSWORD]))
        with pytest.raises(rc.OpsError, match="secret"):
            ev.write("x.json", {"detail": f"pw {CANARY_PASSWORD}"})
        with pytest.raises(rc.OpsError, match="name"):
            ev.write("../escape.json", {})
        assert os.listdir(tmp) == []


# --- interrupted deployment -------------------------------------------------------------------------------------------

def test_interrupted_activation_leaves_marker_evidence_and_blocks_until_resolved(sc):
    class Crashing(FakeService):
        def restart(self):
            super().restart()
            raise RuntimeError("power cut")  # the tool dies mid-activation

    ctx = sc.ctx(answers=[sc.ids["r2"]]); ctx.service = Crashing(sc.world)
    code = ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "--candidate-port", "18001", "deploy", sc.ids["r2"]], ctx)
    assert code == rc.Exit.INTERNAL and "details suppressed" in sc.sink.text
    assert (sc.root / "shared" / "state" / "activation.json").exists()
    assert sc.evidence("result.json")["status"] == "in-progress"   # evidence shows how far it got
    assert deploy(sc, "r2", "--dry-run") == rc.Exit.PREFLIGHT and "interrupted" in sc.sink.text
    # the new release is actually serving and healthy: a human resolves it
    assert sc.run(["resolve-activation", "--dry-run"], tty=False) == 0 and (sc.root / "shared" / "state" / "activation.json").exists()
    assert sc.run(["resolve-activation"], answers=["resolve"]) == 0
    assert not (sc.root / "shared" / "state" / "activation.json").exists()
    assert any(e["event"] == "activation_resolved" for e in sc.log_events())


def test_resolve_activation_refuses_an_unhealthy_service_and_needs_a_tty(sc):
    sc.world.broken.add(sc.ids["r2"])
    deploy(sc, "r2", "--no-auto-rollback")
    assert sc.run(["resolve-activation"], tty=False) == rc.Exit.NO_TTY_OR_ABORT
    assert sc.run(["resolve-activation"], answers=["resolve"]) == rc.Exit.SMOKE
    assert (sc.root / "shared" / "state" / "activation.json").exists()


def test_resolve_activation_without_a_marker_is_a_no_op(sc):
    assert sc.run(["resolve-activation"], answers=[]) == 0 and "no unresolved activation" in sc.sink.text


# --- privileged step (phase 5/10) -------------------------------------------------------------------------------------

class ManualFake(ad.ManualRestartService):
    """The operator 'runs' sudo in another terminal: the fake world restarts."""
    def __init__(self, world, *a):
        super().__init__(*a)
        self.world = world

    def restart(self):
        super().restart()
        FakeService(self.world).restart()


def test_manual_restart_mode_prints_the_command_and_waits_for_confirmation(sc):
    ctx = sc.ctx(answers=[sc.ids["r2"], "restarted"])
    ctx.service = ManualFake(sc.world, ctx.runner, ctx.prompt, ctx.say)
    code = ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "--candidate-port", "18001", "deploy", sc.ids["r2"]], ctx)
    assert code == 0 and "sudo systemctl restart artesa-nfc.service" in sc.sink.text
    assert not any(a[0] == "sudo" for a in sc.world.argv_log)  # the tool itself never ran sudo
    assert sc.evidence("result.json")["status"] == "ok"


def test_manual_restart_not_confirmed_is_an_activation_failure(sc):
    ctx = sc.ctx(answers=[sc.ids["r2"], "no", "restarted"])
    ctx.service = ManualFake(sc.world, ctx.runner, ctx.prompt, ctx.say)
    code = ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "--candidate-port", "18001", "deploy", sc.ids["r2"]], ctx)
    assert code == rc.Exit.ACTIVATION_ROLLED_BACK and sc.current() == sc.ids["r1"]


def test_sudo_is_interactive_never_noninteractive():
    class Recorder:
        guard = None
        def __init__(self): self.calls = []
        def run(self, argv, **kw):
            self.calls.append((argv, kw)); import release_probe as rp; return rp.RunResult(0)
    rec = Recorder()
    ad.SystemdService(rec).restart()
    argv, kw = rec.calls[0]
    assert argv == ["sudo", "systemctl", "restart", "artesa-nfc.service"] and kw["inherit_tty"] is True
    assert "-n" not in argv and "--non-interactive" not in argv


# --- retention (step 21) ------------------------------------------------------------------------------------------------

def test_successful_deploy_applies_retention_keeping_current_and_previous(tmp_path):
    s = Scenario(tmp_path)
    for n in range(1, 8):
        s.release(f"r{n}")
    s.activate("r1")
    assert deploy(s, "r7") == 0
    kept = sorted(p.name for p in (s.root / "releases").iterdir())
    assert len(kept) == 5 and s.ids["r7"] in kept and s.ids["r1"] in kept          # current + previous always kept
    assert s.ids["r2"] not in kept and s.ids["r3"] not in kept                       # oldest others deleted
    retention = s.evidence("retention.json")
    assert retention["keep_releases"] == 5 and sorted(retention["deleted"]) == sorted([s.ids["r2"], s.ids["r3"]])


def test_retention_never_touches_unknown_directories(tmp_path):
    s = Scenario(tmp_path)
    for n in range(1, 8):
        s.release(f"r{n}")
    s.activate("r1")
    (s.root / "releases" / "hand-made-backup").mkdir()
    assert deploy(s, "r7", "--keep-releases", "2") == 0
    assert (s.root / "releases" / "hand-made-backup").is_dir()
    assert sorted(p.name for p in (s.root / "releases").iterdir()) == sorted([s.ids["r1"], s.ids["r7"], "hand-made-backup"])


def test_keep_releases_below_two_is_refused(sc):
    assert deploy(sc, "r2", "--keep-releases", "1", "--dry-run") == rc.Exit.USAGE


# --- run: shared/.env wrapper (D18) ------------------------------------------------------------------------------------

def test_run_alembic_current_uses_the_current_venv_and_shared_env(sc):
    assert sc.run(["run", "alembic", "current"]) == 0
    argv, env = sc.world.argv_log[-1], sc.world.envs_log[-1]
    assert argv[0] == str(sc.root / "releases" / sc.ids["r1"] / "venv" / "bin" / "python") and argv[1:] == ["-m", "alembic", "current"]
    assert env["DATABASE_URL"] == DATABASE_URL and env["APP_ENV"] == "production"
    assert "CLOUDFLARE_API_TOKEN" not in env and "SECRET_KEY" not in env
    assert not any(CANARY_PASSWORD in x for x in argv)
    event = sc.log_events()[-1]
    assert event["event"] == "run" and event["detail"] == "alembic current"


@pytest.mark.parametrize("args", [["alembic", "upgrade", "head"], ["alembic", "downgrade", "-1"], ["alembic", "stamp", "head"], ["alembic"]])
def test_run_never_migrates(sc, args):
    assert sc.run(["run", *args]) == rc.Exit.USAGE
    assert not any("upgrade" in a or "downgrade" in a or "stamp" in a for a in sc.world.argv_log)


def test_run_provision_passes_arguments_through(sc):
    assert sc.run(["run", "provision", "list"]) == 0
    assert sc.world.argv_log[-1][1:] == ["-m", "app.cli.provision", "list"]


def test_run_refuses_a_drifted_current_release(sc):
    (sc.root / "releases" / sc.ids["r1"] / "app" / "main.py").write_text("# hot-patched on the server\n")
    assert sc.run(["run", "alembic", "current"]) == rc.Exit.PREFLIGHT and "modified since extraction" in sc.sink.text


# --- install-tools (bin/, D15) -----------------------------------------------------------------------------------------

LAUNCHER = Path(__file__).resolve().parents[2] / "ops" / "bin" / "artesa-deploy"
TOOL_SOURCES = {f"backend/ops/{n}": (Path(__file__).resolve().parents[2] / "ops" / n).read_text() for n in ad.Tool.TOOL_FILES}


def test_install_tools_installs_a_verified_copy_and_a_launcher(tmp_path):
    s = Scenario(tmp_path)
    files = {**TOOL_SOURCES, "backend/ops/bin/artesa-deploy": LAUNCHER.read_text()}
    s.release("tools", files)
    rid = s.ids["tools"]
    assert s.run(["install-tools", rid, "--dry-run"], tty=False) == 0 and not (s.root / "bin" / "ops").exists()
    assert s.run(["install-tools", rid], answers=[rid]) == 0
    bin_dir = s.root / "bin"
    assert os.readlink(bin_dir / "ops") == f"ops-{rid}"
    assert stat.S_IMODE((bin_dir / "artesa-deploy").stat().st_mode) == 0o755
    info = json.loads((bin_dir / "TOOL.json").read_text())
    assert info["release_id"] == rid and set(info["files"]) == set(ad.Tool.TOOL_FILES)
    for name in ad.Tool.TOOL_FILES:
        assert rc.sha256_file(str(bin_dir / f"ops-{rid}" / name)) == info["files"][name]
        assert stat.S_IMODE((bin_dir / f"ops-{rid}" / name).stat().st_mode) == 0o444
    # the installed launcher really starts the installed tool with the system Python
    proc = subprocess.run([str(bin_dir / "artesa-deploy"), "--help"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and "artesa-deploy" in proc.stdout


def test_install_tools_modes_do_not_depend_on_the_umask(tmp_path):
    # #124: TOOL.json used to inherit the operator's umask (0664 in production with umask 002)
    s = Scenario(tmp_path)
    s.release("tools", {**TOOL_SOURCES, "backend/ops/bin/artesa-deploy": LAUNCHER.read_text()})
    rid = s.ids["tools"]
    bin_dir = s.root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "TOOL.json").write_text("{}\n")
    os.chmod(bin_dir / "TOOL.json", 0o664)  # a previous install left it group-writable
    old = os.umask(0o002)
    try:
        assert s.run(["install-tools", rid], answers=[rid]) == 0
    finally:
        os.umask(old)
    assert stat.S_IMODE((bin_dir / "TOOL.json").stat().st_mode) == 0o644
    info = json.loads((bin_dir / "TOOL.json").read_text())
    assert info["release_id"] == rid and set(info["files"]) == set(ad.Tool.TOOL_FILES)
    for name in ad.Tool.TOOL_FILES:
        assert rc.sha256_file(str(bin_dir / f"ops-{rid}" / name)) == info["files"][name]
        assert stat.S_IMODE((bin_dir / f"ops-{rid}" / name).stat().st_mode) == 0o444
    assert stat.S_IMODE((bin_dir / f"ops-{rid}").stat().st_mode) == 0o555
    assert stat.S_IMODE((bin_dir / "artesa-deploy").stat().st_mode) == 0o755


def test_install_tools_refuses_a_release_without_the_tool(sc):
    assert sc.run(["install-tools", sc.ids["r1"]], answers=[sc.ids["r1"]]) == rc.Exit.PREFLIGHT
    assert not (sc.root / "bin" / "ops").exists()


def test_install_tools_refuses_a_drifted_release(tmp_path):
    s = Scenario(tmp_path)
    s.release("tools", {**TOOL_SOURCES, "backend/ops/bin/artesa-deploy": LAUNCHER.read_text()})
    (s.root / "releases" / s.ids["tools"] / "ops" / "artesa_deploy.py").write_text("# patched\n")
    assert s.run(["install-tools", s.ids["tools"]], answers=[s.ids["tools"]]) == rc.Exit.PREFLIGHT


def test_launcher_in_the_repository_is_executable_and_isolated():
    assert os.access(LAUNCHER, os.X_OK)
    text = LAUNCHER.read_text()
    assert "/usr/bin/python3 -I -B" in text and 'exec ' in text and "set -eu" in text


def test_rollback_is_the_way_out_of_a_failed_migration_activation(mig):
    """After a MIGRATION_DEPLOY fails activation nothing rolls back by itself;
    the human rolls back (ledger-checked, never a downgrade) and that clears
    the marker. A new deploy stays blocked until then."""
    mig.world.broken.add(mig.ids["r_add"])
    assert deploy(mig, "r_add", "--allow-migration") == rc.Exit.ACTIVATION_NO_AUTO_ROLLBACK
    assert mig.run(["deploy", mig.ids["r1"], "--dry-run"], tty=False) == rc.Exit.PREFLIGHT
    assert mig.run(["rollback", "--dry-run"], tty=False) == 0 and "[WARN] layout" in mig.sink.text
    mig.world.broken.clear()
    assert mig.run(["rollback"], answers=[mig.ids["r1"]]) == 0
    assert mig.current() == mig.ids["r1"] and mig.world.db_revision == "ccc333"
    assert not (mig.root / "shared" / "state" / "activation.json").exists()
    [rb] = [d for d in mig.evidence_dirs() if d.name.endswith("-rollback")]
    assert json.loads((rb / "result.json").read_text())["resolves_activation"]["migrated"] is True


def test_extra_keys_in_shared_env_are_a_named_warning_never_a_value(sc):
    assert deploy(sc, "r2", "--dry-run") == 0
    line = next(l for l in sc.sink.lines if "shared/.env configuration" in l)
    assert line.startswith("  [WARN]") and "CLOUDFLARE_API_TOKEN" in line and "SECRET_KEY" in line
    assert CANARY_OTHER not in sc.sink.text and "another-secret-value-123" not in sc.sink.text
