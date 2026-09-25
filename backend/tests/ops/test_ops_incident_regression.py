"""Regression tests for the 2026-09-21 incident: artesa-finanzas shared
ArtesaNFC's directory, venv and port 8000; the NFC unit crash-looped 12742
times; production was a hybrid SCP tree (N-08, ADR-027)."""
import json
import os
from pathlib import Path

import pytest

import artesa_deploy as ad
import deploy_layout as dl
import release_artifact as ra
import release_common as rc
from tests.ops.helpers import Scenario
from tests.ops.test_ops_prepare import Prep


@pytest.fixture()
def sc(tmp_path):
    s = Scenario(tmp_path)
    s.release("r1"); s.release("r2"); s.activate("r1")
    return s


def test_1_another_app_occupying_port_8000_fails_closed_for_deploy_and_rollback_and_nothing_is_killed(sc):
    sc.world.alien_on_port = True
    assert sc.run(["deploy", sc.ids["r2"]], answers=[sc.ids["r2"]]) == rc.Exit.PREFLIGHT
    assert "not by the expected release" in sc.sink.text
    sc.activate("r2", "r1"); sc.world.alien_on_port = True
    assert sc.run(["rollback"], answers=[sc.ids["r1"]]) == rc.Exit.PREFLIGHT
    assert sc.world.restarts == 0
    assert not any(os.path.basename(a[0]) in ("kill", "pkill", "killall", "fuser", "lsof") or (a[0] == "systemctl" and "stop" in a) for a in sc.world.argv_log)
    sc.run(["status", "--json"], tty=False)
    assert json.loads(sc.sink.text)["port"]["state"] == "alien"


def test_2_a_shared_venv_missing_sqlalchemy_is_caught_by_prepare_and_by_verify_deep(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.activate("r1"); s.release("r2", prepared=False)
    (s.root / "releases" / s.ids["r2"]).rename(s.root / "aside")
    ctx = s.ctx(); ctx.runner.handlers = [Prep(s, missing={"sqlalchemy"})]; ctx.python = "py"
    orig = ad.Tool._python_minor; ad.Tool._python_minor = lambda self: "3.14"
    try:
        assert ad.main(["--root", str(s.root), "--rehearsal", "prepare", s.ids["r2"]], ctx) == rc.Exit.PREPARE
    finally:
        ad.Tool._python_minor = orig
    assert "do not match the lock" in s.sink.text and not (s.root / "releases" / s.ids["r2"]).exists()
    # and on an already-prepared release whose venv was later damaged:
    ctx = s.ctx(); ctx.runner.handlers = [Prep(s, missing={"sqlalchemy"})]
    assert ad.main(["--root", str(s.root), "--rehearsal", "verify", s.ids["r1"], "--deep"], ctx) == rc.Exit.ARTIFACT_INVALID
    assert "lock pin not installed or different: sqlalchemy" in s.sink.text


def test_2b_a_venv_that_is_a_symlink_to_a_shared_venv_is_refused(sc):
    rel = sc.root / "releases" / sc.ids["r2"]
    shared = sc.tmp / "finanzas-venv"; (shared / "bin").mkdir(parents=True); (shared / "bin" / "python").write_text("#!/bin/sh\n"); (shared / "pyvenv.cfg").write_text("x")
    import shutil; shutil.rmtree(rel / "venv"); os.symlink(shared, rel / "venv")
    assert sc.run(["deploy", sc.ids["r2"]], answers=[sc.ids["r2"]]) == rc.Exit.PREFLIGHT and "shared venv is never allowed" in sc.sink.text


def test_3_an_unrelated_main_py_in_the_release_root_is_detected_everywhere(sc):
    (sc.root / "releases" / sc.ids["r2"] / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI(title='Artesa-NFC CFO API')\n")
    assert ra.verify_tree(sc.root / "releases" / sc.ids["r2"]) == ["unsafe mode 664: main.py", "unexpected file not in manifest: main.py"] or \
        any("unexpected file not in manifest: main.py" in p for p in ra.verify_tree(sc.root / "releases" / sc.ids["r2"]))
    assert sc.run(["verify", sc.ids["r2"]], tty=False) == rc.Exit.ARTIFACT_INVALID
    assert sc.run(["deploy", sc.ids["r2"], "--dry-run"], tty=False) == rc.Exit.PREFLIGHT
    assert sc.run(["candidate", sc.ids["r2"]], tty=False) == rc.Exit.CANDIDATE


def test_4_a_hybrid_unidentifiable_tree_is_never_a_release(sc):
    hybrid = sc.root / "releases" / "20260919T000000Z-scp-tree"
    hybrid.mkdir(); (hybrid / "app").mkdir(); (hybrid / "main.py").write_text("x")
    infos = {i.name: i for i in dl.list_releases(dl.Layout(sc.root))}
    assert infos["20260919T000000Z-scp-tree"].status == "invalid"
    legacy = sc.root / "releases" / "20260919T000000Z-abcdef123456"        # right name, but no RELEASE.json/manifest
    legacy.mkdir(); (legacy / "app").mkdir()
    assert {i.name: i.status for i in dl.list_releases(dl.Layout(sc.root))}[legacy.name] == "invalid"
    # `current` must be a symlink to a release, never a directory (the old backend dir)
    os.unlink(sc.root / "current"); (sc.root / "current").mkdir(); (sc.root / "current" / "main.py").write_text("x")
    assert sc.run(["deploy", sc.ids["r2"], "--dry-run"], tty=False) == rc.Exit.PREFLIGHT
    assert "not a symlink to a release" in sc.sink.text


def test_5_a_world_readable_env_file_blocks_every_command_that_reads_it(sc):
    sc.activate("r2", "r1")
    os.chmod(sc.root / "shared" / ".env", 0o644)
    for argv in (["deploy", sc.ids["r1"], "--dry-run"], ["candidate", sc.ids["r1"]], ["backup"], ["rollback", "--dry-run"]):
        assert sc.run(argv, tty=True) == rc.Exit.CONFIG, argv
        assert "must be 0600" in sc.sink.text


def test_6_crash_loop_and_shared_directory_configuration_is_detected(sc):
    wd = str(sc.root / "current")
    legacy = dl.ServiceInfo(True, "activating", "auto-restart", 12742, "/home/energias/artesa-nfc-backend", "/home/energias/artesa-nfc-backend/.venv/bin/python")
    assert legacy.crash_looping
    ok, why = dl.unit_points_at_current(dl.Layout(sc.root), legacy)
    assert not ok and "WorkingDirectory" in why
    ctx = sc.ctx(); ctx.service.info = lambda: legacy
    code = ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "deploy", sc.ids["r2"], "--dry-run"], ctx)
    assert code == rc.Exit.PREFLIGHT
    ctx = sc.ctx(); ctx.service.info = lambda: dl.ServiceInfo(True, "activating", "auto-restart", 12742, wd, wd + "/venv/bin/python")
    ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "status", "--json"], ctx)
    assert json.loads(sc.sink.text)["service"]["crash_looping"] is True
    unit = (Path(__file__).resolve().parents[2] / "ops" / "systemd" / "artesa-nfc.service.example").read_text()
    assert "WorkingDirectory=/home/energias/artesa-nfc/current" in unit and "EnvironmentFile=/home/energias/artesa-nfc/shared/.env" in unit
    assert "StartLimitBurst" in unit and "StartLimitIntervalSec" in unit and "12742" in unit          # documented recommendation
    assert "#StartLimitBurst" in unit and "#Restart=" in unit                                          # ...but not applied/decided


def test_7_a_healthy_candidate_runs_while_the_active_production_is_broken(sc):
    sc.world.broken.add(sc.ids["r1"]); sc.world.alien_on_port = True    # production down / port hijacked
    before = sc.world.restarts
    assert sc.run(["candidate", sc.ids["r2"]], tty=False) == 0
    assert "candidate OK" in sc.sink.text and sc.world.restarts == before and sc.current() == sc.ids["r1"]
    assert sc.world.calls == [("candidate", sc.ids["r2"], 18001)]


def test_7b_the_candidate_can_never_take_the_production_port(sc):
    assert sc.run(["candidate", sc.ids["r2"], "--port", "18000"], tty=False) == rc.Exit.CANDIDATE
    assert sc.world.calls == []
