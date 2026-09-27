"""R4 / #130: privileged restart and rollback flow.

The real ``SystemdService``/``ManualRestartService`` run against the fake
World: ``sudo`` and ``systemctl`` are answered by FakeRunner handlers, and
systemd's InvocationID/ExecMainPID change only when the fake unit really
restarts. Reproduces the R3 activation of 2026-09-27 (two sudo timeouts, a
premature 'restarted', an interrupted run) and checks the new behaviour.
"""
from __future__ import annotations

import json
import os

import pytest

import artesa_deploy as ad
import deploy_layout as dl
import release_common as rc
import release_probe as rp
from tests.ops.helpers import FakeService, Scenario

ARGS = ["--rehearsal", "--prod-port", "18000", "--candidate-port", "18001"]


@pytest.fixture
def sc(tmp_path):
    s = Scenario(tmp_path)
    s.release("r1")
    s.release("r2")
    s.activate("r1")
    return s


def really_restart(world) -> rp.RunResult:
    """What a working 'sudo systemctl restart' does: systemd starts current."""
    world.restarts += 1
    world.serving = os.path.basename(os.path.realpath(world.root / "current"))
    return rp.RunResult(0)


class SudoMachine:
    """FakeRunner handler for ``sudo -v`` and ``sudo systemctl restart``.
    ``restarts`` is a list of behaviours, one per restart call."""

    def __init__(self, world, *, preflight_rc: int = 0, restarts=None) -> None:
        self.world, self.preflight_rc = world, preflight_rc
        self.behaviours = list(restarts or [])
        self.sudo_calls: list[list[str]] = []
        self.current_at_preflight: list[str | None] = []

    def __call__(self, argv, env, cwd):
        if argv[:1] != ["sudo"]:
            return None
        self.sudo_calls.append(argv)
        if argv == ["sudo", "-v"]:
            self.current_at_preflight.append(os.path.basename(os.path.realpath(self.world.root / "current")))
            return rp.RunResult(self.preflight_rc, "", "")
        assert argv[:3] == ["sudo", "systemctl", "restart"]
        behaviour = self.behaviours.pop(0) if self.behaviours else really_restart
        return behaviour(self.world)

    @property
    def restart_calls(self) -> int:
        return sum(1 for a in self.sudo_calls if a[:3] == ["sudo", "systemctl", "restart"])


def with_sudo(sc, answers, **machine_kw):
    ctx = sc.ctx(answers=answers)
    machine = SudoMachine(sc.world, **machine_kw)
    ctx.runner.handlers = [machine]
    ctx.service = ad.SystemdService(ctx.runner, out=ctx.say)
    return ctx, machine


def run(sc, ctx, *argv):
    return ad.main(["--root", str(sc.root), *ARGS, *argv], ctx)


def marker_exists(sc) -> bool:
    return (sc.root / "shared" / "state" / "activation.json").exists()


def timeout_without_restart(world) -> rp.RunResult:
    return rp.RunResult(124, "", "timed out after 180s: sudo")


def refused(world) -> rp.RunResult:
    return rp.RunResult(1, "", "")


def not_found(world) -> rp.RunResult:
    return rp.RunResult(127, "", "command not found: sudo")


# --- 2.1 privilege preflight -------------------------------------------------------------------------------------------

def test_sudo_preflight_runs_before_any_symlink_change_and_deploy_succeeds(sc):
    ctx, machine = with_sudo(sc, [sc.ids["r2"]])
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == 0
    assert machine.sudo_calls[0] == ["sudo", "-v"] and machine.current_at_preflight == [sc.ids["r1"]]
    assert machine.restart_calls == 1 and sc.current() == sc.ids["r2"]
    pre = sc.evidence("privilege-preflight.json")
    assert pre["ok"] is True and pre["command"] == "sudo -v" and pre["returncode"] == 0
    assert sc.evidence("final-smoke.json")["restart"]["kind"] == "ok"
    assert "BEFORE any symlink changes" in sc.sink.text


@pytest.mark.parametrize("rc_, text", [(1, "'sudo -v' failed (rc 1)"), (124, "'sudo -v' timed out after 120 s"), (127, "'sudo' was not found")])
def test_sudo_preflight_failure_aborts_before_the_switch(sc, rc_, text):
    before = (sc.current(), sc.previous())
    ctx, machine = with_sudo(sc, [sc.ids["r2"]], preflight_rc=rc_)
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.PREFLIGHT
    assert (sc.current(), sc.previous()) == before and not marker_exists(sc)
    assert machine.restart_calls == 0 and sc.world.restarts == 0
    assert text in sc.sink.text and "Nothing was changed" in sc.sink.text
    pre = sc.evidence("privilege-preflight.json")
    assert pre["ok"] is False and pre["returncode"] == rc_
    result = sc.evidence("result.json")
    assert result["status"] == "failed" and result["exit_code"] == rc.Exit.PREFLIGHT and "Nothing was changed" in result["detail"]
    assert result["current_after"] == sc.ids["r1"]
    assert any(e["event"] == "deploy_failed" for e in sc.log_events())


def test_rollback_preflight_failure_changes_nothing(sc):
    sc.activate("r2", "r1")
    ctx, machine = with_sudo(sc, [sc.ids["r1"]], preflight_rc=1)
    assert run(sc, ctx, "rollback") == rc.Exit.PREFLIGHT
    assert (sc.current(), sc.previous()) == (sc.ids["r2"], sc.ids["r1"]) and not marker_exists(sc)
    assert machine.restart_calls == 0
    assert sc.evidence("result.json")["status"] == "failed"


# --- 2.2 restart diagnostics + 2.3 health-first auto-rollback ------------------------------------------------------

def test_r3_incident_sudo_timeout_is_named_and_rollback_needs_no_second_restart(sc):
    """2026-09-27: sudo waited for a password until the 180 s timeout, twice.
    Now: the timeout is reported as such and, since r1 never stopped
    serving, the rollback restores the symlinks and verifies -- exit 50."""
    ctx, machine = with_sudo(sc, [sc.ids["r2"]], restarts=[timeout_without_restart])
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.ACTIVATION_ROLLED_BACK
    assert machine.restart_calls == 1                      # no blind second sudo
    assert (sc.current(), sc.previous()) == (sc.ids["r1"], None) and not marker_exists(sc)
    assert "timed out after 180 s and systemd did NOT restart the unit" in sc.sink.text
    assert "no restart was needed" in sc.sink.text
    smoke = sc.evidence("final-smoke.json")
    assert smoke["restart"] == {"mode": "sudo", "purpose": "activation", "kind": "sudo-timeout", "returncode": 124,
                                "duration_s": smoke["restart"]["duration_s"], "systemd_restarted": False}
    rb = sc.evidence("rollback.json")
    assert rb["ok"] is True and rb["strategy"] == "health-first" and rb["restart"] is None
    assert rb["activation_restart"]["kind"] == "sudo-timeout" and "timed out" in rb["activation_failure"]
    assert sc.evidence("result.json")["status"] == "rolled-back"


@pytest.mark.parametrize("behaviour, kind, rc_, text", [
    (not_found, "command-not-found", 127, "command not found (rc 127)"),
    (refused, "not-restarted", 1, "systemd did NOT restart the unit (sudo refused or failed before systemctl ran)"),
])
def test_restart_failures_before_systemd_are_distinguished(sc, behaviour, kind, rc_, text):
    ctx, machine = with_sudo(sc, [sc.ids["r2"]], restarts=[behaviour])
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.ACTIVATION_ROLLED_BACK
    assert text in sc.sink.text and machine.restart_calls == 1
    restart = sc.evidence("final-smoke.json")["restart"]
    assert (restart["kind"], restart["returncode"], restart["systemd_restarted"]) == (kind, rc_, False)
    assert sc.evidence("rollback.json")["strategy"] == "health-first"


def test_real_systemctl_failure_is_distinguished_and_the_rollback_restarts(sc):
    """systemd DID restart the unit (new InvocationID) but the new release
    does not come up: not a sudo problem, so the rollback restarts again."""
    sc.world.broken.add(sc.ids["r2"])

    def restarted_but_failed(world):
        really_restart(world)
        return rp.RunResult(1, "", "Job for artesa-nfc.service failed")

    ctx, machine = with_sudo(sc, [sc.ids["r2"]], restarts=[restarted_but_failed])
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.ACTIVATION_ROLLED_BACK
    assert "systemd restarted the unit but reported a failure" in sc.sink.text
    assert sc.evidence("final-smoke.json")["restart"]["kind"] == "systemctl-failed"
    rb = sc.evidence("rollback.json")
    assert rb["strategy"] == "restart" and rb["restart"]["kind"] == "ok" and rb["restart"]["purpose"] == "rollback"
    assert machine.restart_calls == 2 and sc.world.serving == sc.ids["r1"]


def test_second_restart_is_used_when_the_previous_release_is_not_serving(sc):
    sc.world.broken.add(sc.ids["r2"])  # the restart works, the new release does not answer
    ctx, machine = with_sudo(sc, [sc.ids["r2"]])
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.ACTIVATION_ROLLED_BACK
    assert machine.restart_calls == 2 and sc.world.restarts == 2
    assert sc.evidence("rollback.json")["strategy"] == "restart"
    assert sc.current() == sc.ids["r1"] and sc.world.serving == sc.ids["r1"]


def test_structural_sudo_failure_falls_back_to_a_manual_restart(sc, monkeypatch):
    """The sudo mechanism never reached systemd AND r1 is not serving any more:
    the rollback does not repeat sudo; it asks for a manual restart."""
    def timeout_and_process_died(world):
        world.serving = None
        return rp.RunResult(124, "", "timed out")

    ctx, machine = with_sudo(sc, [sc.ids["r2"], "restarted"], restarts=[timeout_and_process_died])

    def manual(tool):
        def operator(message):
            answer = ctx.prompt(message)
            if answer == "restarted":
                really_restart(sc.world)
            return answer
        return ad.ManualRestartService(ctx.runner, operator, ctx.say)

    monkeypatch.setattr(ad.Tool, "_manual_fallback_service", manual)
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.ACTIVATION_ROLLED_BACK
    assert machine.restart_calls == 1
    assert "not repeating it. Falling back to a MANUAL restart" in sc.sink.text and "ROLLBACK RESTART" in sc.sink.text
    rb = sc.evidence("rollback.json")
    assert rb["strategy"] == "manual-fallback" and rb["restart"]["mode"] == "manual" and rb["restart"]["kind"] == "confirmed"
    assert sc.world.serving == sc.ids["r1"]


def test_failed_auto_rollback_keeps_the_cause_and_prints_the_real_state(sc):
    sc.world.broken.update({sc.ids["r1"], sc.ids["r2"]})
    ctx, machine = with_sudo(sc, [sc.ids["r2"]])
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.ACTIVATION_ROLLBACK_FAILED
    rb = sc.evidence("rollback.json")
    assert rb["ok"] is False and rb["error"] and rb["strategy"] == "restart"
    assert "STATE: current ->" in sc.sink.text and "NEXT:" in sc.sink.text and marker_exists(sc)


def test_restart_outcome_of_a_successful_sudo_restart_is_recorded(sc):
    ctx, _ = with_sudo(sc, [sc.ids["r2"]])
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == 0
    restart = sc.evidence("final-smoke.json")["restart"]
    assert restart["kind"] == "ok" and restart["systemd_restarted"] is True and restart["returncode"] == 0 and "duration_s" in restart


def test_unreadable_systemd_state_is_reported_as_unknown(sc):
    ctx, machine = with_sudo(sc, [sc.ids["r2"]], restarts=[refused])
    unreadable = lambda argv, env, cwd: rp.RunResult(1, "", "") if argv[:2] == ["systemctl", "show"] else None
    ctx.runner.handlers = [unreadable, machine]
    # the unit gate reads systemctl too: keep it green by answering info() from the fake service
    ctx.service.info = FakeService(sc.world).info  # type: ignore[method-assign]
    ctx.service.identity = lambda: None            # type: ignore[method-assign]
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.ACTIVATION_ROLLED_BACK
    assert sc.evidence("final-smoke.json")["restart"]["kind"] == "unknown"
    assert "could not be read" in sc.sink.text


# --- 2.4 manual mode -------------------------------------------------------------------------------------------------

def manual_ctx(sc, answers, *, operator_restarts_on=()):
    """Manual mode; the operator really restarts only on the Nth 'restarted'
    (1-based) listed in ``operator_restarts_on``."""
    ctx = sc.ctx(answers=answers)
    seen = {"n": 0}

    def operator(message):
        answer = ctx.prompt(message)
        if "restart has completed" in message and answer.strip() == "restarted":
            seen["n"] += 1
            if seen["n"] in operator_restarts_on:
                really_restart(sc.world)
        return answer

    ctx.service = ad.ManualRestartService(ctx.runner, operator, ctx.say)
    ctx.restart_mode = "manual"
    return ctx


def test_manual_restarted_without_a_real_restart_is_not_accepted(sc):
    """The R3 second attempt: 'restarted' typed before the restart. The tool
    now sees the unchanged InvocationID and does not continue."""
    ctx = manual_ctx(sc, [sc.ids["r2"], "restarted", "restarted", "restarted"])
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.ACTIVATION_ROLLED_BACK
    assert sc.sink.text.count("systemd reports the unit was NOT restarted") == 2
    assert sc.evidence("final-smoke.json")["restart"]["kind"] == "not-restarted"
    assert sc.evidence("rollback.json")["strategy"] == "health-first"  # r1 still serving: no second prompt
    assert sc.sink.text.count("ROLLBACK RESTART") == 0
    assert sc.current() == sc.ids["r1"] and not marker_exists(sc)


def test_manual_restart_can_be_corrected_after_a_premature_confirmation(sc):
    ctx = manual_ctx(sc, [sc.ids["r2"], "restarted", "restarted"], operator_restarts_on=(2,))
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == 0
    assert "systemd reports the unit was NOT restarted" in sc.sink.text
    restart = sc.evidence("final-smoke.json")["restart"]
    assert restart["mode"] == "manual" and restart["kind"] == "confirmed" and restart["systemd_restarted"] is True
    assert sc.current() == sc.ids["r2"] and sc.world.serving == sc.ids["r2"]


def test_manual_prompt_asks_for_a_second_ssh_session_and_names_the_purpose(sc):
    ctx = manual_ctx(sc, [sc.ids["r2"], "restarted"], operator_restarts_on=(1,))
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == 0
    assert "open a SECOND SSH session" in sc.sink.text and "ACTIVATION RESTART" in sc.sink.text
    assert "sudo systemctl restart artesa-nfc.service" in sc.sink.text
    assert sc.evidence("privilege-preflight.json")["mode"] == "manual"
    assert not any(a[0] == "sudo" for a in sc.world.argv_log)


def test_manual_rollback_command_verifies_the_restart(sc):
    sc.activate("r2", "r1")
    ctx = manual_ctx(sc, [sc.ids["r1"], "restarted"], operator_restarts_on=(1,))
    assert run(sc, ctx, "rollback") == 0
    assert "ROLLBACK RESTART" in sc.sink.text
    assert (sc.current(), sc.previous()) == (sc.ids["r1"], sc.ids["r2"]) and sc.world.serving == sc.ids["r1"]
    restart = sc.evidence("final-smoke.json")["restart"]
    assert restart["purpose"] == "rollback" and restart["kind"] == "confirmed"
    assert sc.evidence("result.json")["status"] == "ok"


def test_failed_rollback_command_records_the_cause_and_returns_health_first(sc):
    sc.activate("r2", "r1")
    ctx, machine = with_sudo(sc, [sc.ids["r1"]], restarts=[timeout_without_restart])
    assert run(sc, ctx, "rollback") == rc.Exit.ACTIVATION_ROLLED_BACK   # back on r2, which never stopped
    assert machine.restart_calls == 1
    assert (sc.current(), sc.previous()) == (sc.ids["r2"], sc.ids["r1"])
    rb = sc.evidence("rollback.json")
    assert rb["strategy"] == "health-first" and rb["activation_restart"]["kind"] == "sudo-timeout"
    result = sc.evidence("result.json")
    assert result["status"] == "failed" and "timed out" in result["detail"]


# --- 2.5 interruptions --------------------------------------------------------------------------------------------------

def test_ctrl_c_during_the_health_wait_is_recorded_as_interrupted(sc):
    """current -> r2 while r1 still serves: exactly the state of 2026-09-27."""
    ctx = sc.ctx(answers=[sc.ids["r2"]])
    ctx.service = type("Liar", (FakeService,), {"restart": lambda self, purpose="activation": None})(sc.world)
    real_port_state = ctx.port_state
    fired = {"done": False}

    def port_state(port, expected_cwd, proc="/proc"):
        if not fired["done"] and expected_cwd and os.path.basename(str(expected_cwd)) == sc.ids["r2"]:
            fired["done"] = True
            raise KeyboardInterrupt
        return real_port_state(port, expected_cwd, proc)

    ctx.port_state = port_state
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.NO_TTY_OR_ABORT
    assert (sc.current(), sc.previous()) == (sc.ids["r2"], sc.ids["r1"]) and marker_exists(sc)
    result = sc.evidence("result.json")
    assert result["status"] == "interrupted" and result["exit_code"] == rc.Exit.NO_TTY_OR_ABORT
    assert result["interruption"]["serving"].startswith(sc.ids["r1"]) and result["interruption"]["activation_marker"] is True
    assert "is NOT what serves the port" in sc.sink.text
    event = [e for e in sc.log_events() if e["event"] == "deploy_interrupted"]
    assert event and "marker=present" in event[0]["detail"]


def test_eof_at_the_manual_prompt_is_recorded_as_interrupted(sc):
    ctx = sc.ctx(answers=[sc.ids["r2"]])

    def prompt(message):
        if "restart has completed" in message:
            raise EOFError
        return sc.ids["r2"]

    ctx.service = ad.ManualRestartService(ctx.runner, prompt, ctx.say)
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.NO_TTY_OR_ABORT
    result = sc.evidence("result.json")
    assert result["status"] == "interrupted" and "EOF" in result["interruption"]["reason"]
    assert marker_exists(sc) and sc.current() == sc.ids["r2"]


def test_eof_at_the_initial_confirmation_changes_nothing(sc):
    ctx = sc.ctx()
    ctx.prompt = lambda message: (_ for _ in ()).throw(EOFError())
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.NO_TTY_OR_ABORT
    assert "end of input" in sc.sink.text and sc.current() == sc.ids["r1"] and not sc.evidence_dirs()


def test_ctrl_c_during_a_rollback_is_recorded(sc):
    sc.activate("r2", "r1")
    ctx = sc.ctx(answers=[sc.ids["r1"]])

    class Interrupting(FakeService):
        def restart(self, purpose="activation"):
            raise KeyboardInterrupt

    ctx.service = Interrupting(sc.world)
    assert run(sc, ctx, "rollback") == rc.Exit.NO_TTY_OR_ABORT
    result = sc.evidence("result.json")
    assert result["status"] == "interrupted" and result["kind"] == "manual-rollback"
    assert any(e["event"] == "rollback_interrupted" for e in sc.log_events())


# --- 2.6 resolve-activation closes pending evidence -----------------------------------------------------------------

def test_resolve_activation_closes_an_in_progress_result_without_faking_success(sc):
    class Crashing(FakeService):
        def restart(self, purpose="activation"):
            super().restart()
            raise RuntimeError("power cut")

    ctx = sc.ctx(answers=[sc.ids["r2"]]); ctx.service = Crashing(sc.world)
    assert run(sc, ctx, "deploy", sc.ids["r2"]) == rc.Exit.INTERNAL
    assert sc.evidence("result.json")["status"] == "in-progress"
    assert sc.run(["resolve-activation"], answers=["resolve"]) == 0
    result = sc.evidence("result.json")
    assert result["status"] == "resolved-by-operator" and result["exit_code"] is None
    assert result["resolution"]["command"] == "resolve-activation" and result["resolution"]["current_after"] == sc.ids["r2"]
    assert "not a normal completion" in result["resolution"]["note"]
    assert not any(e["event"] == "deploy_ok" for e in sc.log_events())
    resolved = [e for e in sc.log_events() if e["event"] == "activation_resolved"]
    assert resolved and "closed" in resolved[0]["detail"]


def test_resolve_activation_leaves_a_finished_result_untouched(sc):
    sc.world.broken.add(sc.ids["r2"])
    assert run(sc, sc.ctx(answers=[sc.ids["r2"]]), "deploy", sc.ids["r2"], "--no-auto-rollback") == rc.Exit.ACTIVATION_NO_AUTO_ROLLBACK
    sc.world.broken.clear(); sc.world.serving = sc.ids["r2"]
    before = sc.evidence("result.json")
    assert sc.run(["resolve-activation"], answers=["resolve"]) == 0
    assert sc.evidence("result.json") == before


def test_resolve_activation_ignores_an_unsafe_evidence_name(sc):
    sc.activate("r2", "r1")
    dl.write_activation_marker(dl.Layout(sc.root), {"deploy_id": "x", "from": sc.ids["r1"], "to": sc.ids["r2"], "evidence": "../../bin"})
    assert sc.run(["resolve-activation"], answers=["resolve"]) == 0
    assert not marker_exists(sc)


# --- plumbing ---------------------------------------------------------------------------------------------------------

def test_service_info_reads_invocation_id_and_main_pid(sc):
    info = dl.read_service_info(sc.ctx().runner)
    assert info.invocation_id == "inv0000" and info.main_pid == 4000 and info.identity == ("inv0000", 4000)
    assert dl.ServiceInfo(False).identity is None and dl.ServiceInfo(True, invocation_id="").identity is None


def test_restart_outcome_structural_kinds():
    make = lambda kind: ad.RestartOutcome("sudo", "activation", kind)
    assert all(make(k).structural for k in ("sudo-timeout", "command-not-found", "not-restarted"))
    assert not any(make(k).structural for k in ("ok", "systemctl-failed", "systemctl-timeout", "unknown", "confirmed"))


def test_dry_run_announces_the_privilege_preflight(sc):
    assert sc.run(["deploy", sc.ids["r2"], "--dry-run"], tty=False) == 0
    assert "'sudo -v' preflight BEFORE any symlink change" in sc.sink.text
    ctx = sc.ctx(tty=False); ctx.restart_mode = "manual"
    assert run(sc, ctx, "deploy", sc.ids["r2"], "--dry-run") == 0
    assert "manual, in a SECOND SSH session" in sc.sink.text
    assert sc.world.preflights == 0 and sc.world.restarts == 0
