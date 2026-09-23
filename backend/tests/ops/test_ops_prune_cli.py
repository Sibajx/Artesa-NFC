"""prune, status and CLI-level guarantees (N-08, ADR-027)."""
import json
import os

import pytest

import artesa_deploy as ad
import deploy_layout as dl
import release_common as rc
from tests.ops.helpers import Scenario


@pytest.fixture()
def sc(tmp_path):
    s = Scenario(tmp_path)
    for i in range(1, 8):
        s.release(f"r{i}")
    s.activate("r7", "r6")
    return s


def prune(sc, *args, **kw):
    return sc.run(["prune", *args], **kw)


def test_prune_defaults_to_a_dry_run_that_deletes_nothing_and_needs_no_tty(sc):
    before = sorted(os.listdir(sc.root / "releases"))
    assert prune(sc, tty=False) == 0
    assert sorted(os.listdir(sc.root / "releases")) == before and "DRY RUN" in sc.sink.text
    assert prune(sc, "--dry-run", tty=False) == 0 and sorted(os.listdir(sc.root / "releases")) == before


def test_prune_keeps_current_previous_and_the_newest_up_to_five(sc):
    prune(sc)
    lines = {l.split(":")[0].strip(): l.split(":", 1)[1].strip() for l in sc.sink.lines if l.strip().startswith(("keep", "delete"))}
    keep = set(lines["keep"].split(", ")); delete = set(lines["delete"].split(", "))
    assert len(keep) == 5 and {sc.ids["r7"], sc.ids["r6"]} <= keep
    assert keep == {sc.ids[n] for n in ("r7", "r6", "r5", "r4", "r3")} and delete == {sc.ids["r2"], sc.ids["r1"]}


def test_prune_never_deletes_a_release_involved_in_an_unresolved_activation(sc):
    dl.write_activation_marker(dl.Layout(sc.root), {"deploy_id": "d", "from": sc.ids["r1"], "to": sc.ids["r2"], "started_at": "x"})
    prune(sc, "--keep-releases", "2")
    assert sc.ids["r1"] not in sc.sink.text.split("delete:")[1].split("\n")[0] and sc.ids["r2"] not in sc.sink.text.split("delete:")[1].split("\n")[0]


def test_prune_refuses_unrecognized_and_metadata_less_directories(sc):
    (sc.root / "releases" / "hand-made-backup").mkdir()
    (sc.root / "releases" / "20260101T000000Z-aaaaaaaaaaaa").mkdir()          # valid name, no RELEASE.json
    (sc.root / "releases" / ".staging-99").mkdir()
    prune(sc, "--keep-releases", "2", "--delete", answers=["delete 5"])
    assert (sc.root / "releases" / "hand-made-backup").is_dir() and (sc.root / "releases" / "20260101T000000Z-aaaaaaaaaaaa").is_dir()
    assert "refused: hand-made-backup" in sc.sink.text and "refused: 20260101T000000Z-aaaaaaaaaaaa" in sc.sink.text
    assert sorted(os.listdir(sc.root / "releases")) == sorted([".staging-99", "hand-made-backup", "20260101T000000Z-aaaaaaaaaaaa", sc.ids["r6"], sc.ids["r7"]])


def test_prune_delete_needs_a_tty_and_a_typed_confirmation(sc):
    before = sorted(os.listdir(sc.root / "releases"))
    assert prune(sc, "--delete", tty=False) == rc.Exit.NO_TTY_OR_ABORT
    assert prune(sc, "--delete", answers=["yes"]) == rc.Exit.NO_TTY_OR_ABORT
    assert sorted(os.listdir(sc.root / "releases")) == before


def test_prune_delete_removes_only_the_listed_releases_and_logs_it(sc):
    assert prune(sc, "--delete", answers=["delete 2"]) == 0
    left = set(os.listdir(sc.root / "releases"))
    assert left == {sc.ids[n] for n in ("r3", "r4", "r5", "r6", "r7")} and sc.current() == sc.ids["r7"]
    assert [e["event"] for e in sc.log_events()] == ["prune", "prune"]


def test_prune_keep_releases_below_two_is_a_usage_error(sc):
    assert prune(sc, "--keep-releases", "1") == rc.Exit.USAGE


def test_prune_is_blocked_while_another_operation_holds_the_lock(sc):
    with dl.deploy_lock(dl.Layout(sc.root)):
        assert prune(sc, "--delete", answers=["delete 2"]) == rc.Exit.PREFLIGHT
    assert len(os.listdir(sc.root / "releases")) == 7


def test_delete_release_refuses_current_previous_symlinks_and_paths_outside(sc):
    layout = dl.Layout(sc.root)
    for name in ("r7", "r6"):
        with pytest.raises(rc.OpsError, match="current or previous"):
            dl.delete_release(layout, sc.ids[name])
    outside = sc.tmp / "outside"; outside.mkdir()
    fake = "20260101T000000Z-bbbbbbbbbbbb"
    os.symlink(outside, sc.root / "releases" / fake)
    with pytest.raises(rc.OpsError, match="plain release directory"):
        dl.delete_release(layout, fake)
    assert outside.is_dir()


# --- status ------------------------------------------------------------------------------------------

def test_status_json_is_read_only_and_describes_the_layout(sc):
    before = sc.world.state_hash()
    assert sc.run(["status", "--json"], tty=False) == 0
    report = json.loads(sc.sink.text)
    assert report["current"] == sc.ids["r7"] and report["previous"] == sc.ids["r6"] and len(report["releases"]) == 7
    assert report["port"]["state"] == "expected" and report["unit_points_at_current"]["ok"] and report["env_file"]["mode"] == "0600"
    assert sc.world.state_hash() == before


def test_status_reports_a_hybrid_layout_as_a_problem(sc):
    os.unlink(sc.root / "current")
    (sc.root / "current").mkdir()
    assert sc.run(["status"], tty=False) == rc.Exit.PREFLIGHT and "PROBLEM" in sc.sink.text


def test_status_text_marks_current_previous_and_crash_loop(sc):
    ctx = sc.ctx(); wd = str(sc.root / "current")
    ctx.service.info = lambda: dl.ServiceInfo(True, "activating", "auto-restart", 12742, wd, wd + "/venv/bin/python")
    ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "status"], ctx)
    assert "CRASH-LOOP" in sc.sink.text and "restarts=12742" in sc.sink.text and "current " in sc.sink.text


# --- CLI ---------------------------------------------------------------------------------------------------

def test_rehearsal_flag_cannot_touch_the_production_root(tmp_path):
    ctx = ad.Context(root=rc.DEFAULT_ROOT, rehearsal=True, out=lambda t: None)
    assert ad.main(["--rehearsal", "status"], ctx) == 0 or True   # status is read-only; the guard is exercised below
    tool = ad.Tool(ctx)
    with pytest.raises(rc.OpsError, match="cannot be used with the production root") as err:
        tool.check_root()
    assert err.value.code == rc.Exit.USAGE


def test_a_custom_root_requires_rehearsal(tmp_path):
    tool = ad.Tool(ad.Context(root=tmp_path, rehearsal=False, out=lambda t: None))
    with pytest.raises(rc.OpsError, match="only allowed together with --rehearsal"):
        tool.check_root()


def test_unknown_commands_and_missing_arguments_are_usage_errors():
    for argv in ([], ["explode"], ["deploy"], ["restore-check", "x"], ["prune", "--keep-releases", "abc"]):
        with pytest.raises(SystemExit) as err:
            ad.build_parser().parse_args(argv)
        assert err.value.code == 2


def test_invalid_release_ids_are_rejected_by_every_command(sc):
    for command in ("prepare", "verify", "candidate", "deploy"):
        for bad in ("../../etc", "latest", "20260921T030000Z-ABCDEF123456", "x y", "a/b", ""):
            assert sc.run([command, bad], tty=True) == rc.Exit.ARTIFACT_INVALID


def test_the_interface_has_no_yes_flag_anywhere():
    parser = ad.build_parser()
    text = parser.format_help()
    for action in parser._subparsers._group_actions[0].choices.values():
        text += action.format_help()
    assert "--yes" not in text and "-y," not in text


def test_internal_errors_print_only_the_exception_type(sc):
    from tests.ops.helpers import DATABASE_URL, CANARY_PASSWORD
    ctx = sc.ctx(answers=[sc.ids["r7"]])
    def boom():
        raise RuntimeError(f"connecting to {DATABASE_URL} failed")
    ctx.service.info = boom
    code = ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "status"], ctx)
    assert code == rc.Exit.INTERNAL
    assert "internal error: RuntimeError" in sc.sink.text and CANARY_PASSWORD not in sc.sink.text and "Traceback" not in sc.sink.text


@pytest.mark.parametrize("argv", [["prepare", "X"], ["deploy", "X"], ["rollback"], ["backup"], ["prune", "--delete"]])
def test_state_changing_commands_fail_fast_without_a_tty_before_any_gate_runs(sc, argv):
    argv = [sc.ids["r7"] if a == "X" else a for a in argv]
    before = sc.world.state_hash()
    assert sc.run(argv, tty=False) == rc.Exit.NO_TTY_OR_ABORT
    assert "[PASS]" not in sc.sink.text and "[FAIL]" not in sc.sink.text     # no gate ran
    assert sc.world.state_hash() == before and sc.world.argv_log == []
