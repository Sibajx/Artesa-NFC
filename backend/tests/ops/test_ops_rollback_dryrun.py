"""rollback and --dry-run behaviour (N-08, ADR-027)."""
import pytest

import release_common as rc
from tests.ops.helpers import Scenario, migration_files


@pytest.fixture()
def sc(tmp_path):
    s = Scenario(tmp_path)
    s.release("r1"); s.release("r2")
    s.activate("r2", "r1")
    return s


def rollback(sc, *args, **kw):
    target = sc.previous() if "--to" not in args else args[args.index("--to") + 1]
    return sc.run(["rollback", *args], answers=[target], **kw)


# --- rollback -----------------------------------------------------------------------------

def test_code_only_rollback_swaps_current_and_previous(sc):
    assert rollback(sc) == 0
    assert (sc.current(), sc.previous()) == (sc.ids["r1"], sc.ids["r2"])
    assert sc.world.restarts == 1 and sc.world.serving == sc.ids["r1"]
    assert not any("downgrade" in " ".join(a) for a in sc.world.argv_log)
    assert [e["event"] for e in sc.log_events()] == ["rollback_start", "rollback_ok"]


def test_rollback_to_a_specific_older_release(tmp_path):
    s = Scenario(tmp_path); s.release("r0"); s.release("r1"); s.release("r2"); s.activate("r2", "r1")
    assert s.run(["rollback", "--to", s.ids["r0"]], answers=[s.ids["r0"]]) == 0
    assert (s.current(), s.previous()) == (s.ids["r0"], s.ids["r2"])


def test_rollback_needs_a_tty_and_a_confirmation(sc):
    before = sc.world.state_hash()
    assert rollback(sc, tty=False) == rc.Exit.NO_TTY_OR_ABORT
    assert sc.run(["rollback"], answers=["nope"]) == rc.Exit.NO_TTY_OR_ABORT
    assert sc.world.state_hash() == before


def test_no_previous_release(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.activate("r1")
    assert s.run(["rollback"]) == rc.Exit.PREFLIGHT and "no previous release" in s.sink.text


def test_previous_that_drifted_is_refused(sc):
    (sc.root / "releases" / sc.ids["r1"] / "main.py").write_text("# unrelated app\n")
    assert rollback(sc) == rc.Exit.PREFLIGHT and "unexpected file not in manifest: main.py" in sc.sink.text
    assert sc.current() == sc.ids["r2"] and sc.world.restarts == 0


def test_previous_that_is_not_prepared_is_refused(tmp_path):
    s = Scenario(tmp_path); s.release("r1", prepared=False); s.release("r2"); s.activate("r2", "r1")
    assert s.run(["rollback"], answers=[s.ids["r1"]]) == rc.Exit.PREFLIGHT


def test_rollback_to_the_current_release_is_refused(sc):
    assert sc.run(["rollback", "--to", sc.ids["r2"]], answers=[sc.ids["r2"]]) == rc.Exit.PREFLIGHT


def test_incompatible_database_blocks_the_rollback(tmp_path):
    s = Scenario(tmp_path); s.release("r1")
    s.release("r_break", migration_files("ccc333", "bbb222", "breaking")); s.activate("r_break", "r1")
    s.world.db_revision = "ccc333"
    assert s.run(["rollback"], answers=[s.ids["r1"]]) == rc.Exit.ROLLBACK_INCOMPATIBLE
    assert "not additive" in s.sink.text and s.current() == s.ids["r_break"] and s.world.restarts == 0


def test_rollback_over_an_additive_migration_is_allowed(tmp_path):
    s = Scenario(tmp_path); s.release("r1")
    s.release("r_add", migration_files("ccc333", "bbb222", "additive")); s.activate("r_add", "r1")
    s.world.db_revision = "ccc333"
    assert s.run(["rollback"], answers=[s.ids["r1"]]) == 0 and s.current() == s.ids["r1"]
    assert s.world.db_revision == "ccc333"  # forward-only: the schema is never downgraded


def test_rollback_when_the_database_is_behind_the_target_is_refused(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.release("r_add", migration_files("ccc333", "bbb222", "additive"))
    s.activate("r1", "r_add")  # "previous" is the newer release, database still at bbb222
    assert s.run(["rollback"], answers=[s.ids["r_add"]]) == rc.Exit.ROLLBACK_INCOMPATIBLE and "behind" in s.sink.text


def test_alien_process_on_the_port_blocks_rollback(sc):
    sc.world.alien_on_port = True
    assert rollback(sc) == rc.Exit.PREFLIGHT and sc.world.restarts == 0


def test_a_rollback_target_that_fails_health_returns_to_the_release_it_left(sc):
    sc.world.broken.add(sc.ids["r1"])
    assert rollback(sc) == rc.Exit.ACTIVATION_ROLLED_BACK
    assert (sc.current(), sc.previous()) == (sc.ids["r2"], sc.ids["r1"]) and sc.world.serving == sc.ids["r2"]
    assert sc.world.restarts == 2


# --- dry run: read-only gates, no mutation ---------------------------------------------------

def test_deploy_dry_run_mutates_nothing_and_needs_no_tty(sc):
    before = sc.world.state_hash()
    assert sc.run(["deploy", sc.ids["r2"] if False else sc.ids["r1"], "--dry-run"], tty=False) == 0
    sc2 = sc  # deploy of the active release is a warn no-op; use a real candidate below
    sc.release("r3"); before = sc.world.state_hash()
    assert sc.run(["deploy", sc.ids["r3"], "--dry-run"], tty=False) == 0
    assert sc.world.state_hash() == before
    assert sc.world.restarts == 0 and sc.world.calls == []  # no candidate was started
    assert not any(("upgrade" in a) or any(x.startswith("--file=") for x in a) for a in sc.world.argv_log)
    assert sc.log_events() == []


def test_deploy_dry_run_shows_the_plan(sc):
    sc.release("r3")
    sc.run(["deploy", sc.ids["r3"], "--dry-run"], tty=False)
    text = sc.sink.text
    for needle in (f"target={sc.ids['r3']}", f"source={sc.ids['r2']}", "class=code-only", "alembic bbb222 -> bbb222", "backup: not required",
                   "migration: no", "candidate: 127.0.0.1:18001", "rollback target: " + sc.ids["r2"], "auto-rollback on activation failure: yes", "dry run"):
        assert needle in text, needle
    assert sc.ids["r3"] != sc.ids["r2"] and "commit" in text


def test_migration_dry_run_shows_backup_and_migration_and_still_mutates_nothing(tmp_path):
    s = Scenario(tmp_path); s.release("r1"); s.activate("r1"); s.release("r_add", migration_files("ccc333", "bbb222", "additive"))
    s.record_rehearsal(); before = s.world.state_hash()
    assert s.run(["deploy", s.ids["r_add"], "--dry-run", "--allow-migration"], tty=False) == 0
    assert "class=migration/additive" in s.sink.text and "backup: REQUIRED" in s.sink.text and "alembic bbb222 -> ccc333" in s.sink.text
    assert "auto-rollback on activation failure: NO" in s.sink.text
    assert s.world.state_hash() == before and s.world.db_revision == "bbb222"
    assert not any(a[0] == "pg_dump" and "--version" not in a for a in s.world.argv_log)


def test_rollback_dry_run_mutates_nothing(sc):
    before = sc.world.state_hash()
    assert sc.run(["rollback", "--dry-run"], tty=False) == 0
    assert sc.world.state_hash() == before and "dry run" in sc.sink.text and sc.log_events() == []


@pytest.mark.parametrize("break_it,code", [
    (lambda s: setattr(s.world, "alien_on_port", True), rc.Exit.PREFLIGHT),
    (lambda s: setattr(s.world, "db_revision", "mystery"), rc.Exit.ALEMBIC),
    (lambda s: (s.root / "shared" / ".env").chmod(0o644), rc.Exit.CONFIG),
    (lambda s: (s.root / "releases" / s.ids["r3"] / "app" / "main.py").write_text("x\n"), rc.Exit.PREFLIGHT),
])
def test_dry_run_reports_the_same_gate_errors_as_a_real_deploy(sc, break_it, code):
    sc.release("r3"); break_it(sc)
    assert sc.run(["deploy", sc.ids["r3"], "--dry-run"], tty=False) == code
    dry_text = sc.sink.text
    assert sc.run(["deploy", sc.ids["r3"]], answers=[sc.ids["r3"]]) == code
    fails = lambda t: sorted(l for l in t.splitlines() if "[FAIL]" in l)
    assert fails(dry_text) == fails(sc.sink.text) and fails(dry_text)
    assert sc.world.restarts == 0
