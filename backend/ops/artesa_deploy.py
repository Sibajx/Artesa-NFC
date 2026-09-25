#!/usr/bin/env python3
"""artesa-deploy: immutable, artifact-based backend releases (N-08, ADR-027).

    artesa-deploy status [--json]
    artesa-deploy prepare <id> [--dry-run]
    artesa-deploy verify <id> [--deep]
    artesa-deploy candidate <id> [--port 8001]
    artesa-deploy backup
    artesa-deploy deploy <id> --expect-commit <sha> [--dry-run] [--allow-migration]
                         [--no-auto-rollback] [--restart-mode sudo|manual] [--keep-releases 5]
    artesa-deploy rollback [--to <id>] [--dry-run] [--restart-mode sudo|manual]
    artesa-deploy resolve-activation [--dry-run]
    artesa-deploy restore-check <dump> (--ephemeral | --scratch-server-url-env VAR) [--candidate-release <id>]
    artesa-deploy prune [--keep-releases 5] [--delete]
    artesa-deploy run alembic current|heads|history|check
    artesa-deploy run provision <provisioning CLI arguments>
    artesa-deploy install-tools <id>

Standard library only. Every state-changing command needs a real terminal
(no ``--yes``); ``--dry-run`` runs every read-only gate and mutates nothing.
Nothing here calls ``alembic downgrade``, kills a foreign process, reads
GitHub or runs ``sudo`` non-interactively. Exit codes: docs/DEPLOYMENT.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deploy_db as ddb  # noqa: E402
import deploy_layout as dl  # noqa: E402
import release_artifact as ra  # noqa: E402
import release_common as rc  # noqa: E402
import release_probe as rp  # noqa: E402

MIN_FREE_BYTES = 300 * 1024 * 1024
HEALTH_TIMEOUT = 45.0
_PIP_IGNORED = frozenset({"pip", "setuptools", "wheel"})


# --- context (everything with a side effect is injectable for tests) -------------

class SystemdService:
    """Restarts/inspects the production unit. ``sudo`` prompts on the real
    terminal, which is why state-changing commands require a TTY."""

    def __init__(self, runner: rp.Runner, name: str = rc.SERVICE_NAME) -> None:
        self.runner, self.name = runner, name

    def restart(self) -> None:
        result = self.runner.run(["sudo", "systemctl", "restart", self.name], inherit_tty=True, timeout=180)
        if result.returncode != 0:
            raise rc.OpsError(rc.Exit.ACTIVATION_ROLLBACK_FAILED, f"systemctl restart {self.name} failed")

    def info(self) -> dl.ServiceInfo:
        return dl.read_service_info(self.runner, self.name)


class ManualRestartService(SystemdService):
    """The privileged step is performed by the operator, by hand, in another
    terminal: the tool prints the exact command and waits for a typed
    confirmation. For operators who do not want the tool to call sudo."""

    def __init__(self, runner: rp.Runner, prompt: Callable[[str], str], out: Callable[[str], None], name: str = rc.SERVICE_NAME) -> None:
        super().__init__(runner, name)
        self.prompt, self.out = prompt, out

    def restart(self) -> None:
        self.out("PRIVILEGED STEP (run it yourself in another terminal):")
        self.out(f"    sudo systemctl restart {self.name}")
        answer = self.prompt("Type 'restarted' once the command has completed (anything else aborts the activation): ")
        if answer.strip() != "restarted":
            raise rc.OpsError(rc.Exit.ACTIVATION_ROLLBACK_FAILED, "the operator did not confirm the service restart")


def _default_public_check(url: str) -> tuple[bool, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "artesa-deploy"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 -- fixed https URL
            ctype = response.headers.get("Content-Type", "")
            ok = response.status == 200 and "application/json" in ctype
            return ok, f"status {response.status}, content-type {ctype.split(';')[0] or '-'}"
    except urllib.error.HTTPError as exc:
        return False, f"status {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return False, f"unreachable ({type(exc).__name__})"


@dataclass
class Context:
    root: Path
    rehearsal: bool = False
    prod_port: int = rc.PRODUCTION_PORT
    candidate_port: int = rc.CANDIDATE_PORT
    environ: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    guard: rp.SecretGuard = field(default_factory=rp.SecretGuard)
    runner: rp.Runner | None = None
    service: object | None = None
    is_tty: Callable[[], bool] = lambda: sys.stdin.isatty() and sys.stdout.isatty()
    prompt: Callable[[str], str] = input
    clock: Callable[[], datetime] = rc.utc_now
    sleep: Callable[[float], None] = time.sleep
    out: Callable[[str], None] = print
    fetch: Callable[..., rp.HttpResult] = rp.http_request
    port_state: Callable[..., rp.PortState] = rp.port_owner_state
    candidate: Callable[..., rp.CandidateReport] = rp.run_candidate
    public_check: Callable[[str], tuple[bool, str]] | None = _default_public_check
    python: str = sys.executable
    make_venv: Callable[[Path], None] | None = None
    health_timeout: float = HEALTH_TIMEOUT
    pg_bindir: str | None = None
    restart_mode: str = "sudo"

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if self.runner is None:
            self.runner = rp.Runner(self.guard)
        else:
            self.guard = self.runner.guard
        if self.service is None:
            if self.restart_mode == "manual":
                self.service = ManualRestartService(self.runner, self.prompt, self.say)
            else:
                self.service = SystemdService(self.runner)

    def say(self, text: str = "") -> None:
        self.out(self.guard.scrub(text))


@dataclass
class Gate:
    name: str
    status: str  # pass | fail | warn | skip
    detail: str = ""
    code: int = 0


class Tool:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.layout = dl.Layout(ctx.root)
        self.deploy_id = f"d-{ctx.clock().strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
        self._log: dl.DeployLog | None = None

    # -- helpers -------------------------------------------------------------------

    def log(self) -> dl.DeployLog:
        if self._log is None:
            self._log = dl.DeployLog(self.layout, self.ctx.guard, self.deploy_id, self.ctx.clock)
        return self._log

    def require_tty(self, dry_run: bool) -> None:
        if not dry_run and not self.ctx.is_tty():
            raise rc.OpsError(rc.Exit.NO_TTY_OR_ABORT, "this command changes state and needs an interactive terminal (stdin and stdout must be a TTY); use --dry-run to inspect")

    def confirm(self, expected: str) -> None:
        answer = self.ctx.prompt(f"Type '{expected}' to continue (anything else aborts): ")
        if answer.strip() != expected:
            raise rc.OpsError(rc.Exit.NO_TTY_OR_ABORT, "aborted by the operator")

    def check_root(self) -> None:
        production_root = Path(os.path.realpath(rc.DEFAULT_ROOT))
        here = Path(os.path.realpath(self.ctx.root))
        if self.ctx.rehearsal and here == production_root:
            raise rc.OpsError(rc.Exit.USAGE, "--rehearsal cannot be used with the production root")
        if not self.ctx.rehearsal and here != production_root:
            raise rc.OpsError(rc.Exit.USAGE, "a non-default --root is only allowed together with --rehearsal")
        if not self.layout.root.is_dir():
            raise rc.OpsError(rc.Exit.PREFLIGHT, "the deployment root does not exist")

    def check_channel(self, release: dict) -> None:
        if release["channel"] == rc.CHANNEL_REHEARSAL and not self.ctx.rehearsal:
            raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, "a rehearsal artifact cannot be used on the production root")

    def load_env(self) -> rp.EnvFile:
        env = rp.read_env_file(self.layout.env_file, enforce_mode=True)
        self.ctx.guard.merge(env.guard)
        rp.validate_production_env(env.values)
        return env

    def env_for_children(self, env: rp.EnvFile) -> dict[str, str]:
        return rp.child_env(env.values)

    def current_id(self) -> str | None:
        return dl.read_link(self.layout, self.layout.current)

    def previous_id(self) -> str | None:
        return dl.read_link(self.layout, self.layout.previous)

    def load_release(self, release_id: str) -> dict:
        path = self.layout.release_dir(release_id) / "RELEASE.json"
        try:
            return rc.validate_release_json(json.loads(path.read_text(encoding="utf-8")), expected_id=release_id)
        except (OSError, ValueError):
            raise rc.OpsError(rc.Exit.PREFLIGHT, f"release {release_id} has no readable RELEASE.json") from None

    def load_ledger(self, release_id: str) -> dict[str, dict]:
        try:
            return rc.parse_ledger((self.layout.release_dir(release_id) / rc.LEDGER_PATH).read_bytes())
        except OSError:
            raise rc.OpsError(rc.Exit.PREFLIGHT, f"release {release_id} has no migration ledger") from None

    def prepared_problems(self, release_id: str) -> list[str]:
        directory = self.layout.release_dir(release_id)
        if os.path.islink(directory) or not directory.is_dir():
            return ["release directory does not exist or is not a real directory"]
        marker = dl.read_prepared(directory)
        if marker is None:
            return ["release is not marked prepared (no valid .prepared marker)"]
        problems = ra.verify_tree(directory)
        try:
            if marker["release_json_sha256"] != rc.sha256_file(str(directory / "RELEASE.json")):
                problems.append("RELEASE.json changed after prepare")
            if marker["content_sha256"] != self.load_release(release_id)["artifact"]["content_sha256"]:
                problems.append("prepared marker does not match RELEASE.json")
        except (OSError, rc.OpsError):
            problems.append("prepared marker cannot be checked against RELEASE.json")
        venv = directory / "venv"
        if os.path.islink(venv) or not venv.is_dir():
            problems.append("venv is missing or is a symlink (a shared venv is never allowed)")
        elif not (venv / "bin" / "python").exists() or not (venv / "pyvenv.cfg").exists():
            problems.append("venv is incomplete")
        elif not os.path.realpath(venv).startswith(os.path.realpath(directory) + os.sep):
            problems.append("venv resolves outside its release")
        return problems

    # -- gates ------------------------------------------------------------------------

    @staticmethod
    def _gate(gates: list[Gate], name: str, fn: Callable[[], object]) -> object | None:
        try:
            outcome = fn()
        except rc.OpsError as exc:
            gates.append(Gate(name, "fail", exc.message, exc.code))
            return None
        if isinstance(outcome, tuple) and outcome and outcome[0] in ("warn", "skip"):
            gates.append(Gate(name, outcome[0], outcome[1]))
            return outcome[2] if len(outcome) > 2 else None
        gates.append(Gate(name, "pass", outcome if isinstance(outcome, str) else ""))
        return outcome

    def disk_gate(self) -> str:
        free = shutil.disk_usage(self.layout.root).free
        if free < MIN_FREE_BYTES:
            raise rc.OpsError(rc.Exit.PREFLIGHT, f"only {free // (1024 * 1024)} MiB free; need {MIN_FREE_BYTES // (1024 * 1024)} MiB")
        return f"{free // (1024 * 1024)} MiB free"

    def show_gates(self, gates: list[Gate]) -> None:
        for gate in gates:
            mark = {"pass": "PASS", "fail": "FAIL", "warn": "WARN", "skip": "SKIP"}[gate.status]
            self.ctx.say(f"  [{mark}] {gate.name}" + (f": {gate.detail}" if gate.detail else ""))

    @staticmethod
    def first_failure(gates: list[Gate]) -> Gate | None:
        return next((g for g in gates if g.status == "fail"), None)

    def gates_common(self, gates: list[Gate], *, need_service_unit: bool, marker_is_warning: bool = False) -> None:
        def layout() -> object:
            self.check_root()
            current, previous = self.current_id(), self.previous_id()
            if dl.lock_is_held(self.layout):
                raise rc.OpsError(rc.Exit.PREFLIGHT, "another deploy operation holds the deployment lock")
            marker = dl.read_activation_marker(self.layout)
            if marker and marker_is_warning:
                # rollback IS the documented way out of a failed activation
                return ("warn", f"unresolved activation {marker.get('from')} -> {marker.get('to')}; a successful rollback resolves it", None)
            if marker:
                raise rc.OpsError(rc.Exit.PREFLIGHT, "a previous activation was interrupted or left unresolved (shared/state/activation.json); roll back or run 'resolve-activation' first")
            return f"current={current or 'none'} previous={previous or 'none'}"

        self._gate(gates, "layout / current / previous / lock", layout)

        self._gate(gates, "disk space", self.disk_gate)
        if need_service_unit:
            def unit() -> object:
                info = self.ctx.service.info()
                ok, detail = dl.unit_points_at_current(self.layout, info)
                if not ok:
                    raise rc.OpsError(rc.Exit.PREFLIGHT, detail)
                if info.crash_looping:
                    return ("warn", f"service is crash-looping ({info.n_restarts} restarts); a deploy is the fix, but investigate", None)
                return detail

            self._gate(gates, "systemd unit runs from <root>/current", unit)

            def port() -> object:
                current = self.current_id()
                expected = self.layout.current if current else None
                state = self.ctx.port_state(self.ctx.prod_port, expected)
                if not state.ok:
                    raise rc.OpsError(rc.Exit.PREFLIGHT, state.detail)
                if state.state == "free":
                    return ("warn", state.detail + " (service not running?)", None)
                return state.detail

            self._gate(gates, f"port {self.ctx.prod_port} held only by the active release", port)

    # -- status ---------------------------------------------------------------------------

    def cmd_status(self, as_json: bool) -> int:
        report: dict[str, object] = {"root_exists": self.layout.root.is_dir()}
        problems: list[str] = []
        try:
            report["current"] = self.current_id()
            report["previous"] = self.previous_id()
        except rc.OpsError as exc:
            problems.append(exc.message)
        infos = dl.list_releases(self.layout)
        report["releases"] = [{"id": i.name, "status": i.status, "channel": i.channel, "commit": i.commit, "detail": i.detail} for i in infos]
        try:
            st = os.lstat(self.layout.env_file)
            report["env_file"] = {"exists": True, "mode": f"{st.st_mode & 0o777:04o}", "regular": os.path.isfile(self.layout.env_file) and not os.path.islink(self.layout.env_file)}
        except OSError:
            report["env_file"] = {"exists": False}
        state = self.ctx.port_state(self.ctx.prod_port, self.layout.current if report.get("current") else None)
        report["port"] = {"port": self.ctx.prod_port, "state": state.state, "detail": state.detail}
        info = self.ctx.service.info()
        report["service"] = {"available": info.available, "active": info.active_state, "sub": info.sub_state, "restarts": info.n_restarts, "crash_looping": info.crash_looping}
        ok, detail = dl.unit_points_at_current(self.layout, info)
        report["unit_points_at_current"] = {"ok": ok, "detail": detail}
        report["lock_held"] = dl.lock_is_held(self.layout)
        report["unresolved_activation"] = dl.read_activation_marker(self.layout) is not None
        report["problems"] = problems
        if as_json:
            self.ctx.say(json.dumps(report, indent=2, sort_keys=True))
        else:
            self.ctx.say(f"root            {self.layout.root}")
            self.ctx.say(f"current         {report.get('current') or '-'}")
            self.ctx.say(f"previous        {report.get('previous') or '-'}")
            self.ctx.say(f"port {self.ctx.prod_port}       {state.state}: {state.detail}")
            self.ctx.say(f"service         {info.active_state or 'unknown'}/{info.sub_state or '-'} restarts={info.n_restarts}" + ("  CRASH-LOOP" if info.crash_looping else ""))
            self.ctx.say(f"unit -> current {'yes' if ok else 'NO: ' + detail}")
            self.ctx.say(f"shared/.env     {report['env_file']}")
            self.ctx.say(f"deploy lock     {'HELD' if report['lock_held'] else 'free'}   unresolved activation: {report['unresolved_activation']}")
            self.ctx.say("releases:")
            for entry in report["releases"]:  # type: ignore[union-attr]
                tags = ("current " if entry["id"] == report.get("current") else "") + ("previous" if entry["id"] == report.get("previous") else "")
                self.ctx.say(f"  {entry['id']}  {entry['status']:<10} {entry['channel'] or '-':<10} {entry['commit'] or '-':<12} {tags} {entry['detail']}")
            for problem in problems:
                self.ctx.say(f"PROBLEM: {problem}")
        return 0 if not problems else int(rc.Exit.PREFLIGHT)

    # -- verify -------------------------------------------------------------------------------

    def cmd_verify(self, release_id: str, deep: bool) -> int:
        rc.validate_release_id(release_id)
        found = False
        directory = self.layout.release_dir(release_id)
        artifact = self.layout.artifact_path(release_id)
        if artifact.exists() or os.path.islink(artifact):
            found = True
            verified = ra.verify_artifact(artifact, expected_id=release_id)
            self.check_channel(verified.release)
            self.ctx.say(f"artifact OK      {artifact.name} (content_sha256 {verified.release['artifact']['content_sha256'][:16]}...)")
        if directory.exists():
            found = True
            problems = self.prepared_problems(release_id)
            if deep and not problems:
                problems += self.freeze_problems(directory, self.load_release(release_id))
            if problems:
                for item in problems:
                    self.ctx.say(f"  problem: {item}")
                raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, f"release {release_id} failed verification ({len(problems)} problem(s))")
            self.ctx.say(f"release OK       releases/{release_id} is prepared and identical to its manifest" + (" (deep: venv matches the lock)" if deep else ""))
        if not found:
            raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, "no artifact in incoming/ and no release directory for this id")
        return 0

    def freeze_problems(self, directory: Path, release: dict) -> list[str]:
        lock = rc.parse_lock((directory / rc.LOCK_PATH).read_text(encoding="utf-8"), target_python=release["build"]["target_python"])
        result = self.ctx.runner.run([str(directory / "venv" / "bin" / "python"), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"], env=rp.child_env({}), timeout=120)
        if result.returncode != 0:
            return ["pip list failed inside the release venv"]
        try:
            installed = {rc.normalize_package(p["name"]): p["version"] for p in json.loads(result.stdout) if rc.normalize_package(p["name"]) not in _PIP_IGNORED}
        except (ValueError, KeyError, TypeError):
            return ["pip list returned an unreadable answer"]
        problems = [f"lock pin not installed or different: {n}=={v}" for n, v in lock.items() if installed.get(n) != v]
        problems += [f"package installed but not in the lock: {n}" for n in installed if n not in lock]
        return problems

    # -- prepare -------------------------------------------------------------------------------

    def cmd_prepare(self, release_id: str, dry_run: bool) -> int:
        self.require_tty(dry_run)  # fail fast: before any gate does work
        rc.validate_release_id(release_id)
        gates: list[Gate] = []
        verified = self._gate(gates, "artifact + sidecar + manifest + RELEASE.json", lambda: (
            ra.verify_artifact(self.layout.artifact_path(release_id), expected_id=release_id)
        ))
        release = verified.release if verified else None

        def channel() -> str:
            self.check_root()
            self.check_channel(release)
            return f"channel {release['channel']}"

        if release:
            self._gate(gates, "channel allowed on this root", channel)

            def python() -> str:
                have = self._python_minor()
                if have != release["build"]["target_python"]:
                    raise rc.OpsError(rc.Exit.PREPARE, f"this host's Python is {have}; the release targets {release['build']['target_python']}")
                return f"python {have}"

            self._gate(gates, "interpreter matches the release target", python)
        self._gate(gates, "shared/.env valid (needed for the import check)", lambda: self.load_env() and "valid")
        self._gate(gates, "disk space", self.disk_gate)
        already = False
        directory = self.layout.release_dir(release_id)
        if directory.exists():
            def existing() -> object:
                problems = self.prepared_problems(release_id)
                if not problems:
                    return ("warn", "release is already prepared and identical to its manifest; nothing to do", True)
                raise rc.OpsError(rc.Exit.PREPARE, "releases/<id> exists but is not a clean prepared release; remove it by hand after inspecting it")

            already = bool(self._gate(gates, "existing release directory", existing))
        self.ctx.say(f"prepare {release_id}{' (dry run)' if dry_run else ''}")
        self.show_gates(gates)
        failure = self.first_failure(gates)
        if failure:
            return failure.code
        if already:
            return 0
        if dry_run:
            self.ctx.say("plan: extract to staging -> rename to releases/<id> -> create venv there -> pip install --require-hashes --only-binary=:all: --no-deps -r requirements-prod.lock -> freeze==lock -> compile -> import app.main -> write .prepared")
            self.ctx.say("(dry run: nothing was written)")
            return 0
        dl.ensure_layout(self.layout)
        with dl.deploy_lock(self.layout):
            self.log().event("prepare_start", command="prepare", target_release=release_id, git_sha=release["git"]["commit"][:12])
            try:
                self._prepare_release(verified, release_id)
            except rc.OpsError as exc:
                self.log().event("prepare_failed", command="prepare", target_release=release_id, exit_code=exc.code, detail=exc.message[:200])
                raise
            self.log().event("prepare_ok", command="prepare", target_release=release_id, exit_code=0)
        self.ctx.say(f"prepared {release_id}: ready for 'candidate' and 'deploy'")
        return 0

    def _python_minor(self) -> str:
        if self.ctx.python == sys.executable:
            return f"{sys.version_info.major}.{sys.version_info.minor}"
        result = self.ctx.runner.run([self.ctx.python, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"], env=rp.child_env({}), timeout=15)
        return result.stdout.strip()

    def _prepare_release(self, verified: ra.VerifiedArtifact, release_id: str) -> None:
        release = verified.release
        env = self.load_env()
        final = self.layout.release_dir(release_id)
        staging_parent = self.layout.releases / f"{dl.STAGING_PREFIX}{os.getpid()}"
        created_final = False
        try:
            os.mkdir(staging_parent, 0o700)
            staged = staging_parent / release_id
            ra.extract_artifact(verified, staged)
            problems = ra.verify_tree(staged)
            if problems:
                raise rc.OpsError(rc.Exit.PREPARE, "staged tree does not match its manifest: " + "; ".join(problems[:3]))
            os.rename(staged, final)
            created_final = True
            venv = final / "venv"
            if self.ctx.make_venv is not None:
                self.ctx.make_venv(venv)
            else:
                result = self.ctx.runner.run([self.ctx.python, "-m", "venv", str(venv)], env=rp.child_env({}), timeout=120)
                if result.returncode != 0:
                    raise rc.OpsError(rc.Exit.PREPARE, "could not create the release venv (python3-venv installed?)")
            python = str(venv / "bin" / "python")
            pip = self.ctx.runner.run(
                [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--require-hashes", "--only-binary=:all:", "--no-deps", "-r", str(final / rc.LOCK_PATH)],
                env=rp.child_env({}), timeout=1200,
            )
            if pip.returncode != 0:
                tail = self.ctx.guard.scrub((pip.stderr.strip().splitlines() or ["no output"])[-1])[:160]
                raise rc.OpsError(rc.Exit.PREPARE, f"dependency install failed: {tail}")
            drift = self.freeze_problems(final, release)
            if drift:
                raise rc.OpsError(rc.Exit.PREPARE, "installed packages do not match the lock: " + "; ".join(drift[:3]))
            check = self.ctx.runner.run([python, "-m", "pip", "check", "--disable-pip-version-check"], env=rp.child_env({}), timeout=120)
            if check.returncode != 0:
                raise rc.OpsError(rc.Exit.PREPARE, "pip check reports broken requirements")
            compile_code = "import sys,pathlib\nfor r in ('app','alembic','ops'):\n    for f in pathlib.Path(r).rglob('*.py'):\n        compile(f.read_text(encoding='utf-8'), str(f), 'exec')\n"
            comp = self.ctx.runner.run([python, "-B", "-c", compile_code], env=rp.child_env({}), cwd=final, timeout=120)
            if comp.returncode != 0:
                raise rc.OpsError(rc.Exit.PREPARE, "a source file does not compile")
            imp = self.ctx.runner.run([python, "-B", "-c", "import app.main"], env=self.env_for_children(env), cwd=final, timeout=120)
            if imp.returncode != 0:
                tail = self.ctx.guard.scrub((imp.stderr.strip().splitlines() or ["no output"])[-1])[:160]
                raise rc.OpsError(rc.Exit.PREPARE, f"import app.main failed inside the release venv: {tail}")
            version = self.ctx.runner.run([python, "-c", "import sys;print(sys.version.split()[0])"], env=rp.child_env({}), timeout=15).stdout.strip()
            dl.write_prepared(final, {
                "schema_version": 1, "release_id": release_id, "prepared_at": rc.utc_iso(self.ctx.clock()),
                "tool_version": rc.TOOL_VERSION, "python": version,
                "lockfile_sha256": release["deps"]["lockfile_sha256"],
                "release_json_sha256": rc.sha256_file(str(final / "RELEASE.json")),
                "content_sha256": release["artifact"]["content_sha256"],
                "archive_sha256": verified.archive_sha256,
            })
        except BaseException:
            if created_final and final.is_dir() and not os.path.islink(final) and dl.read_prepared(final) is None:
                shutil.rmtree(final, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)

    # -- candidate -------------------------------------------------------------------------------------

    def run_candidate(self, release_id: str, port: int, env_values: dict[str, str]) -> rp.CandidateReport:
        problems = self.prepared_problems(release_id)
        if problems:
            raise rc.OpsError(rc.Exit.CANDIDATE, "release is not a clean prepared release: " + "; ".join(problems[:3]))
        release = self.load_release(release_id)
        self.layout.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = self.ctx.clock().strftime("%Y%m%dT%H%M%S%fZ")
        log_path = self.layout.state / f"candidate-{release_id}-{stamp}.log"
        return self.ctx.candidate(
            release_dir=self.layout.release_dir(release_id), env=rp.child_env(env_values), port=port,
            log_path=log_path, release=release, guard=self.ctx.guard, fetch=self.ctx.fetch,
        )

    def report_candidate(self, report: rp.CandidateReport) -> None:
        for check in report.checks:
            self.ctx.say(f"  [{'PASS' if check.ok else 'FAIL'}] {check.name}" + (f": {check.detail}" if check.detail and not check.ok else ""))

    def cmd_candidate(self, release_id: str, port: int) -> int:
        rc.validate_release_id(release_id)
        self.check_root()
        env = self.load_env()
        if port == self.ctx.prod_port:
            raise rc.OpsError(rc.Exit.CANDIDATE, "the candidate must not use the production port")
        report = self.run_candidate(release_id, port, env.values)
        self.ctx.say(f"candidate {release_id} on 127.0.0.1:{port} (log 0600: {Path(report.log_path).name})")
        self.report_candidate(report)
        if not report.ok:
            raise rc.OpsError(rc.Exit.SMOKE, "candidate smoke checks failed")
        self.ctx.say("candidate OK (process killed; the active service was not touched)")
        return 0

    # -- database gates shared by deploy/rollback --------------------------------------------------------

    def db_state(self, release_id: str, env: rp.EnvFile) -> ddb.DbState:
        return ddb.read_db_state(self.ctx.runner, self.layout.release_dir(release_id), self.env_for_children(env))

    # -- backup ---------------------------------------------------------------------------------------------

    def cmd_backup(self) -> int:
        self.require_tty(False)
        self.check_root()
        env = self.load_env()
        current = self.current_id()
        if not current:
            raise rc.OpsError(rc.Exit.PREFLIGHT, "there is no current release to run the database probe with")
        dl.ensure_layout(self.layout)
        with dl.deploy_lock(self.layout):
            state = self.db_state(current, env)
            result = ddb.create_backup(runner=self.ctx.runner, layout=self.layout, env_file=env, state=state, active_release=current, clock=self.ctx.clock)
            self.log().event("backup", command="backup", source_release=current, alembic_from=state.revision, backup=result.path.name, backup_sha256=result.sha256, exit_code=0)
        self.ctx.say(f"backup {result.path.name}  {result.size} bytes  sha256 {result.sha256[:16]}...  alembic {result.alembic_revision or 'none'}")
        return 0

    # -- deploy ---------------------------------------------------------------------------------------------------

    def restore_target(self, server_major: int) -> ddb.EphemeralCluster:
        bindir = Path(self.ctx.pg_bindir) if self.ctx.pg_bindir else ddb.default_pg_bindir(server_major)
        return ddb.EphemeralCluster(runner=self.ctx.runner, bindir=bindir, parent=self.layout.restore_tmp, min_major=server_major)

    def artifact_gate(self, release_id: str) -> ra.VerifiedArtifact:
        """Steps 1-3: the artifact still in incoming/ is re-verified (sidecar
        checksum, MANIFEST, RELEASE.json) and must be the very bytes this
        release was prepared from."""
        verified = ra.verify_artifact(self.layout.artifact_path(release_id), expected_id=release_id)
        marker = dl.read_prepared(self.layout.release_dir(release_id)) or {}
        if marker.get("archive_sha256") != verified.archive_sha256:
            raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, "incoming artifact is not the one this release was prepared from")
        if marker.get("content_sha256") != verified.release["artifact"]["content_sha256"]:
            raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, "incoming artifact content differs from the prepared release")
        return verified

    def commit_gate(self, release: dict, expect_commit: str | None) -> str:
        """Step 4: a production root only runs a production-channel artifact
        (built from history reachable from origin/main) whose commit is the
        one the operator names from the reviewed main branch/CI run."""
        self.check_channel(release)
        commit = release["git"]["commit"]
        if expect_commit is None:
            if not self.ctx.rehearsal:
                raise rc.OpsError(rc.Exit.USAGE, "--expect-commit <sha> (at least 12 hex characters, from origin/main / CI) is required on the production root")
            return f"{release['channel']} commit {commit[:12]} (no --expect-commit: rehearsal root)"
        if not re.fullmatch(r"[0-9a-f]{12,40}", expect_commit):
            raise rc.OpsError(rc.Exit.USAGE, "--expect-commit must be 12-40 lowercase hex characters")
        if not commit.startswith(expect_commit):
            raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, f"release commit {commit[:12]} is not the expected commit {expect_commit[:12]}")
        return f"{release['channel']} commit {commit[:12]} == --expect-commit" + (f", reachable from {release['git']['reachable_from']}" if release["git"]["reachable_from"] else "")

    def cmd_deploy(self, release_id: str, dry_run: bool, allow_migration: bool, auto_rollback: bool, skip_public: bool,
                   expect_commit: str | None = None, keep_releases: int = 5) -> int:
        self.require_tty(dry_run)  # fail fast: before any gate does work
        rc.validate_release_id(release_id)
        if keep_releases < 2:
            raise rc.OpsError(rc.Exit.USAGE, "--keep-releases must be at least 2 (current + previous)")
        gates: list[Gate] = []
        self.gates_common(gates, need_service_unit=True)
        release: dict | None = None
        env: rp.EnvFile | None = None
        verified: ra.VerifiedArtifact | None = None

        def target() -> str:
            nonlocal release
            problems = self.prepared_problems(release_id)
            if problems:
                raise rc.OpsError(rc.Exit.PREFLIGHT, "; ".join(problems[:3]))
            release = self.load_release(release_id)
            return f"releases/{release_id} identical to its MANIFEST"

        self._gate(gates, "target release prepared and identical to its manifest", target)
        if release:
            verified = self._gate(gates, "artifact checksum + MANIFEST + RELEASE.json re-verified", lambda: self.artifact_gate(release_id))
            self._gate(gates, "commit authorized", lambda: self.commit_gate(release, expect_commit))

            def deps() -> str:
                problems = self.freeze_problems(self.layout.release_dir(release_id), release)
                if problems:
                    raise rc.OpsError(rc.Exit.PREFLIGHT, "release venv differs from the lock: " + "; ".join(problems[:3]))
                return "release venv == requirements-prod.lock (hash-pinned install)"

            self._gate(gates, "dependencies match the lock", deps)

        def envgate() -> object:
            nonlocal env
            env = self.load_env()
            if env.ignored_keys:
                # systemd's EnvironmentFile= would hand these to the service too
                return ("warn", f"valid, but carries {len(env.ignored_keys)} key(s) the app does not use ({', '.join(env.ignored_keys)[:120]}); "
                                "keep only APP_ENV, DATABASE_URL, DEBUG, CORS_ALLOWED_ORIGINS", env)
            return "0600, APP_ENV=production, DATABASE_URL set (not shown), DEBUG off, CORS ok"

        self._gate(gates, "shared/.env configuration", envgate)

        def ports() -> str:
            if self.ctx.candidate_port in rc.RESERVED_PORTS or self.ctx.candidate_port == self.ctx.prod_port:
                raise rc.OpsError(rc.Exit.USAGE, f"candidate port {self.ctx.candidate_port} is reserved (production {rc.PRODUCTION_PORT}, finanzas {rc.FINANZAS_PORT})")
            return f"candidate 127.0.0.1:{self.ctx.candidate_port}"

        self._gate(gates, "candidate port is not reserved", ports)
        current = None
        try:
            current = self.current_id()
        except rc.OpsError:
            pass
        if current == release_id:
            gates.append(Gate("target differs from current", "warn", "already the active release; nothing to deploy"))
        plan: ddb.MigrationPlan | None = None
        state: ddb.DbState | None = None
        if release and env and not any(g.status == "fail" and g.name.startswith("target") for g in gates):
            def database() -> str:
                nonlocal plan, state
                state = self.db_state(release_id, env)
                other = None
                if current and current != release_id:
                    try:
                        other = {r["revision"] for r in self.load_release(current)["alembic"]["revisions"]}
                    except rc.OpsError:
                        other = None
                plan = ddb.plan_migration(release, self.load_ledger(release_id), state, other_known=other)
                return f"database at {plan.db_revision or 'empty'}, release head {plan.head}, class {plan.deployment_class}"

            self._gate(gates, "database revision known and compatible", database)

            def policy() -> object:
                assert plan is not None and state is not None
                if plan.deployment_class == rc.CLASS_BREAKING:
                    raise rc.OpsError(rc.Exit.MIGRATION_NOT_AUTHORIZED, "a pending migration is classified 'breaking': refused in V1 (no automatic maintenance mode)")
                if plan.pending and not allow_migration:
                    raise rc.OpsError(rc.Exit.MIGRATION_NOT_AUTHORIZED, f"{len(plan.pending)} pending migration(s) ({plan.deployment_class}); re-run with --allow-migration")
                if plan.pending:
                    try:
                        major, text = ddb.pg_dump_major(self.ctx.runner)
                    except rc.OpsError as exc:
                        raise rc.OpsError(rc.Exit.BACKUP, exc.message) from None
                    if major < state.server_major:
                        raise rc.OpsError(rc.Exit.BACKUP, f"pg_dump {major} is older than the server {state.server_major}")
                    target = self.restore_target(state.server_major)
                    try:
                        initdb_major, _ = ddb.pg_dump_major(self.ctx.runner, str(target.bindir / "initdb"))
                    except rc.OpsError:
                        raise rc.OpsError(rc.Exit.BACKUP, f"no PostgreSQL server binaries for the restore-check in {target.bindir} (use --pg-bindir)") from None
                    if initdb_major < state.server_major:
                        raise rc.OpsError(rc.Exit.BACKUP, f"initdb {initdb_major} is older than the server {state.server_major}")
                    return f"MIGRATION_DEPLOY authorized: backup + restore-check on a disposable cluster + ledger ok ({text})"
                return "CODE_ONLY: no migration, no backup required"

            if plan is not None:
                self._gate(gates, "migration policy", policy)
            else:
                gates.append(Gate("migration policy", "skip", "skipped: the database revision could not be planned"))
        rollback_target = current if current and current != release_id else None
        self.ctx.say(f"deploy {release_id}{' (dry run)' if dry_run else ''}")
        self.show_gates(gates)
        failure = self.first_failure(gates)
        if plan:
            kind = rc.deploy_type(plan.deployment_class)
            self.ctx.say(f"  plan: source={current or 'none'} target={release_id} type={kind} class={plan.deployment_class} alembic {plan.db_revision or 'empty'} -> {plan.head}")
            self.ctx.say(f"  backup: {'REQUIRED (this run), then restore-check on a disposable cluster' if plan.pending else 'not required (code-only)'}   migration: {'yes, alembic upgrade head (forward-only)' if plan.pending else 'no'}")
            self.ctx.say(f"  candidate: 127.0.0.1:{self.ctx.candidate_port} smoke, then activate ({'manual' if self.ctx.restart_mode == 'manual' else 'interactive sudo'} restart); rollback target: {rollback_target or 'none (first deployment)'}")
            self.ctx.say(f"  auto-rollback on activation failure: {'yes (code-only)' if (auto_rollback and not plan.pending) else 'NO'}   retention: keep {keep_releases} releases")
        if failure:
            return failure.code
        if dry_run:
            self.ctx.say("(dry run: nothing was written, no service was touched)")
            return 0
        if current == release_id:
            return 0
        assert release and env and plan and state and verified
        self.confirm(release_id)
        return self._execute_deploy(release_id, release, env, plan, state, current, auto_rollback, skip_public, verified, gates, keep_releases)

    def _execute_deploy(self, release_id: str, release: dict, env: rp.EnvFile, plan: ddb.MigrationPlan, state: ddb.DbState,
                        current: str | None, auto_rollback: bool, skip_public: bool, verified: ra.VerifiedArtifact,
                        gates: list[Gate], keep_releases: int) -> int:
        dl.ensure_layout(self.layout)
        migrated = False
        kind = rc.deploy_type(plan.deployment_class)
        with dl.deploy_lock(self.layout):
            log = self.log()
            ev = dl.Evidence.create(self.layout, self.ctx.guard, self.ctx.clock(), release["git"]["commit"])
            self.evidence = ev
            result = {"deploy_id": self.deploy_id, "source_release": current, "target_release": release_id,
                      "git_commit": release["git"]["commit"], "deploy_type": kind, "deployment_class": plan.deployment_class,
                      "started_at": rc.utc_iso(self.ctx.clock()), "status": "in-progress", "exit_code": None,
                      "operator": dl.operator_name(), "tool_version": rc.TOOL_VERSION, "restart_mode": self.ctx.restart_mode}
            ev.write("result.json", result)
            ev.write("release.json", release)
            ev.write("artifact.json", {"file": rc.artifact_filename(release_id), "archive_sha256": verified.archive_sha256,
                                       "content_sha256": release["artifact"]["content_sha256"], "file_count": release["artifact"]["file_count"],
                                       "manifest_verified": True, "release_tree_verified": True})
            ev.write("dependencies.json", {"lockfile": release["deps"]["lockfile"], "lockfile_sha256": release["deps"]["lockfile_sha256"],
                                           "install_mode": release["deps"]["install_mode"], "venv_matches_lock": True})
            ev.write("preflight.json", {"gates": [{"name": g.name, "status": g.status, "detail": g.detail} for g in gates]})
            alembic = {"before": plan.db_revision, "target": plan.head, "after": None, "pending": plan.pending,
                       "deployment_class": plan.deployment_class, "deploy_type": kind}
            ev.write("alembic.json", alembic)
            log.event("deploy_start", command="deploy", source_release=current, target_release=release_id, git_sha=release["git"]["commit"][:12],
                      deployment_class=plan.deployment_class, alembic_from=plan.db_revision, alembic_to=plan.head, detail=f"evidence {ev.directory.name}")
            try:
                if plan.pending:
                    backup = ddb.create_backup(runner=self.ctx.runner, layout=self.layout, env_file=env, state=state, active_release=current,
                                               clock=self.ctx.clock, active_commit=self._commit_of(current), target_release=release_id, deploy_id=self.deploy_id)
                    ev.write("backup.json", {"dump": backup.path.name, "sha256": backup.sha256, "size_bytes": backup.size,
                                             "alembic_revision": backup.alembic_revision, "pg_dump_version": backup.pg_dump_version})
                    log.event("backup", command="deploy", backup=backup.path.name, backup_sha256=backup.sha256)
                    self.ctx.say(f"backup {backup.path.name} verified (sha256 {backup.sha256[:16]}...)")
                    report = ddb.restore_check(
                        runner=self.ctx.runner, dump=backup.path, target=self.restore_target(state.server_major),
                        probe_release_dir=self.layout.release_dir(release_id), app_env=env.values,
                        migrate_release_dir=self.layout.release_dir(release_id), expect_head=plan.head,
                    )
                    ev.write("restore-check.json", report.evidence())
                    log.event("restore_check", command="deploy", backup_sha256=report.dump_sha256,
                              checks=[f"counts_match={report.counts_match}", f"migration_rehearsal={report.migration_rehearsal}", f"destroyed={report.cleanup_ok}"],
                              exit_code=0 if report.ok else int(rc.Exit.BACKUP))
                    if not report.ok:
                        raise rc.OpsError(rc.Exit.BACKUP, "restore-check failed (" + "; ".join(report.problems[:2]) + "); the production database was NOT migrated")
                    self.ctx.say(f"restore-check OK on a disposable cluster: {report.tables} tables, row counts identical, migration rehearsed ({report.migration_rehearsal})")
                    migrated = True  # from here a failure means the database may have changed
                    after = self._migrate(release_id, env, plan)
                    alembic["after"] = after.revision
                    ev.write("alembic.json", alembic)
                    self.ctx.say(f"migrated to {plan.head}")
                cand = self.run_candidate(release_id, self.ctx.candidate_port, env.values)
                self.report_candidate(cand)
                ev.write("candidate-smoke.json", self._checks_evidence(cand.checks, ok=cand.ok, port=self.ctx.candidate_port, log=Path(cand.log_path).name))
                if not cand.ok:
                    raise rc.OpsError(rc.Exit.SMOKE, "candidate smoke checks failed" + ("; the database WAS migrated (additive), the active service is untouched" if migrated else "; nothing was changed"))
                code = self._activate(release_id, release, env, plan, current, auto_rollback, migrated)
            except rc.OpsError as exc:
                log.event("deploy_failed", command="deploy", target_release=release_id, exit_code=exc.code, detail=exc.message[:200])
                self._finish(result, "failed", exc.code, detail=exc.message[:300], alembic=alembic, env=env, release_id=release_id)
                raise
            if code != 0:
                log.event("deploy_failed", command="deploy", target_release=release_id, exit_code=code)
                status = {int(rc.Exit.ACTIVATION_ROLLED_BACK): "rolled-back", int(rc.Exit.ACTIVATION_NO_AUTO_ROLLBACK): "failed-no-auto-rollback"}.get(code, "failed")
                self._finish(result, status, code, alembic=alembic, env=env, release_id=release_id)
                return code
            log.event("deploy_ok", command="deploy", source_release=current, target_release=release_id, exit_code=0)
            retention = self._retention(keep_releases)
            ev.write("retention.json", retention)
        code = 0
        if not skip_public and self.ctx.public_check is not None:
            code = self._public_check(release_id)
        result["public_edge"] = "skipped" if skip_public or self.ctx.public_check is None else ("ok" if code == 0 else "failed")
        self._finish(result, "ok" if code == 0 else "edge-failure", code, alembic=alembic, env=env, release_id=release_id)
        self.ctx.say(f"evidence: shared/state/deployments/{ev.directory.name}/")
        return code

    evidence: dl.Evidence | None = None

    @staticmethod
    def _checks_evidence(checks: list[rp.Check], **extra: object) -> dict:
        return {**extra, "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks]}

    def _commit_of(self, release_id: str | None) -> str | None:
        if not release_id:
            return None
        try:
            return self.load_release(release_id)["git"]["commit"]
        except rc.OpsError:
            return None

    def _finish(self, result: dict, status: str, code: int, *, alembic: dict, env: rp.EnvFile, release_id: str, detail: str = "") -> None:
        """Close the evidence: final status, exit code, Alembic 'after'."""
        ev = self.evidence
        if ev is None:
            return
        if alembic.get("after") is None:
            try:
                alembic["after"] = self.db_state(release_id, env).revision
            except rc.OpsError:
                alembic["after"] = "unknown (database probe failed)"
            ev.write("alembic.json", alembic)
        result.update({"status": status, "exit_code": int(code), "finished_at": rc.utc_iso(self.ctx.clock())})
        if detail:
            result["detail"] = detail
        try:
            result["current_after"], result["previous_after"] = self.current_id(), self.previous_id()
        except rc.OpsError:
            result["current_after"] = result["previous_after"] = "unreadable"
        ev.write("result.json", result)

    def _retention(self, keep: int) -> dict:
        """Step 21: keep the newest ``keep`` releases (always current and
        previous). Directories the tool does not recognise are never touched."""
        plan = dl.plan_prune(self.layout, keep, set())
        deleted = []
        for release_id in plan.delete:
            try:
                dl.delete_release(self.layout, release_id)
                deleted.append(release_id)
                self.log().event("prune", command="deploy", target_release=release_id, exit_code=0)
            except (rc.OpsError, OSError):
                self.ctx.say(f"  retention: could not delete {release_id}; left in place")
        if deleted:
            self.ctx.say(f"  retention: deleted {', '.join(deleted)}")
        return {"keep_releases": keep, "kept": plan.keep, "deleted": deleted, "refused": [n for n, _ in plan.refused]}

    def _migrate(self, release_id: str, env: rp.EnvFile, plan: ddb.MigrationPlan) -> ddb.DbState:
        directory = self.layout.release_dir(release_id)
        result = self.ctx.runner.run([str(directory / "venv" / "bin" / "python"), "-m", "alembic", "upgrade", "head"], env=self.env_for_children(env), cwd=directory, timeout=900)
        if result.returncode != 0:
            tail = self.ctx.guard.scrub((result.stderr.strip().splitlines() or ["no output"])[-1])[:160]
            raise rc.OpsError(rc.Exit.MIGRATION_FAILED, f"alembic upgrade failed (exit {result.returncode}): {tail}. The database state is UNCERTAIN: inspect it, restore from the backup if needed. No automatic rollback was attempted")
        after = self.db_state(release_id, env)
        if after.alembic_versions != [plan.head]:
            raise rc.OpsError(rc.Exit.MIGRATION_FAILED, "alembic reported success but the database is not at the release head; state is UNCERTAIN")
        return after

    def _wait_healthy(self, release_id: str) -> list[rp.Check]:
        directory = self.layout.release_dir(release_id)
        deadline = time.monotonic() + self.ctx.health_timeout
        checks: list[rp.Check] = []
        while True:
            state = self.ctx.port_state(self.ctx.prod_port, directory)
            healthy = False
            if state.state == "expected":
                try:
                    healthy = self.ctx.fetch(self.ctx.prod_port, "GET", "/health", timeout=3).status == 200
                except OSError:
                    healthy = False
            if healthy or time.monotonic() >= deadline:
                break
            self.ctx.sleep(1.0)
        checks.append(rp.Check(f"port {self.ctx.prod_port} held by the new release", state.state == "expected", state.detail))
        if state.state == "expected":
            checks.extend(rp.run_smoke(self.ctx.prod_port, self.load_release(release_id), self.ctx.guard, self.ctx.fetch))
        return checks

    def _switch(self, new_current: str, new_previous: str | None) -> None:
        if new_previous:
            dl.atomic_symlink(self.layout, self.layout.previous, new_previous)
        dl.atomic_symlink(self.layout, self.layout.current, new_current)

    def _activate(self, release_id: str, release: dict, env: rp.EnvFile, plan: ddb.MigrationPlan, current: str | None, auto_rollback: bool, migrated: bool) -> int:
        old_previous = self.previous_id()
        dl.write_activation_marker(self.layout, {"deploy_id": self.deploy_id, "from": current, "to": release_id, "started_at": rc.utc_iso(self.ctx.clock()),
                                                 "deploy_type": rc.deploy_type(plan.deployment_class), "migrated": migrated,
                                                 "evidence": self.evidence.directory.name if self.evidence else None})
        failure = ""
        checks: list[rp.Check] = []
        try:
            self._switch(release_id, current)
            self.ctx.service.restart()
            checks = self._wait_healthy(release_id)
            bad = [c for c in checks if not c.ok]
            if bad:
                failure = "; ".join(f"{c.name}" for c in bad[:3])
        except rc.OpsError as exc:
            failure = exc.message
        if self.evidence:
            self.evidence.write("final-smoke.json", self._checks_evidence(checks, ok=not failure, port=self.ctx.prod_port, release=release_id, failure=failure))
        if not failure:
            dl.clear_activation_marker(self.layout)
            self.ctx.say(f"ACTIVE {release_id} (previous {current or 'none'}); service healthy and smoke checks passed")
            return 0
        self.ctx.say(f"ACTIVATION FAILED: {failure}")
        if migrated or not auto_rollback or not current:
            why = "a migration ran" if migrated else ("--no-auto-rollback was given" if not auto_rollback else "there is no previous release to return to")
            if self.evidence:
                self.evidence.write("rollback.json", {"attempted": False, "reason": why, "policy": "automatic rollback only for CODE_ONLY deployments"})
            self.ctx.say(f"NOT rolling back automatically ({why}). State: current -> {release_id}, previous -> {current or 'none'}. Decide by hand: 'artesa-deploy rollback' (checked against the migration ledger) or fix forward, then 'artesa-deploy resolve-activation'. Activation marker left in shared/state/activation.json.")
            return int(rc.Exit.ACTIVATION_NO_AUTO_ROLLBACK)
        return self._auto_rollback(current, old_previous)

    def _auto_rollback(self, current: str, old_previous: str | None) -> int:
        self.ctx.say(f"returning to {current}")
        checks: list[rp.Check] = []
        code = int(rc.Exit.ACTIVATION_ROLLBACK_FAILED)
        try:
            self._switch(current, old_previous)
            if old_previous is None:
                try:
                    os.unlink(self.layout.previous)
                except FileNotFoundError:
                    pass
            self.ctx.service.restart()
            checks = self._wait_healthy(current)
            if all(c.ok for c in checks):
                dl.clear_activation_marker(self.layout)
                self.ctx.say(f"ROLLED BACK to {current}; service healthy")
                code = int(rc.Exit.ACTIVATION_ROLLED_BACK)
        except rc.OpsError:
            pass
        if self.evidence:
            self.evidence.write("rollback.json", self._checks_evidence(checks, attempted=True, automatic=True, to=current,
                                                                      ok=code == int(rc.Exit.ACTIVATION_ROLLED_BACK)))
        if code != int(rc.Exit.ACTIVATION_ROLLED_BACK):
            self.ctx.say("AUTOMATIC ROLLBACK DID NOT RESTORE A HEALTHY SERVICE. Manual intervention required.")
        return code

    def _public_check(self, release_id: str) -> int:
        url = f"{rc.PUBLIC_API_BASE}/api/v1/artisans"
        for attempt in range(3):
            ok, detail = self.ctx.public_check(url)  # type: ignore[misc]
            if ok:
                self.ctx.say(f"public edge OK: {detail}")
                return 0
            if attempt < 2:
                self.ctx.sleep(3.0)
        self.ctx.say(f"PUBLIC EDGE CHECK FAILED ({detail}) while the local origin is healthy. This is a Cloudflare/tunnel routing problem, not a code problem: NOT rolling back. Check the tunnel's public hostname routes.")
        return int(rc.Exit.EDGE_FAILURE)

    # -- rollback ------------------------------------------------------------------------------------------------------

    def cmd_rollback(self, to: str | None, dry_run: bool) -> int:
        self.require_tty(dry_run)  # fail fast: before any gate does work
        gates: list[Gate] = []
        self.gates_common(gates, need_service_unit=True, marker_is_warning=True)
        current = previous = None
        try:
            current, previous = self.current_id(), self.previous_id()
        except rc.OpsError:
            pass
        target_id = to or previous
        env: rp.EnvFile | None = None
        compat = None

        def target() -> str:
            if not current:
                raise rc.OpsError(rc.Exit.PREFLIGHT, "there is no current release")
            if not target_id:
                raise rc.OpsError(rc.Exit.PREFLIGHT, "there is no previous release to roll back to")
            rc.validate_release_id(target_id)
            if target_id == current:
                raise rc.OpsError(rc.Exit.PREFLIGHT, "the target is already the current release")
            problems = self.prepared_problems(target_id)
            if problems:
                raise rc.OpsError(rc.Exit.PREFLIGHT, "rollback target is not a clean prepared release (drift?): " + "; ".join(problems[:3]))
            self.check_channel(self.load_release(target_id))
            return f"{current} -> {target_id}"

        ok_target = self._gate(gates, "rollback target exists, is prepared and identical to its manifest", target)

        def envgate() -> str:
            nonlocal env
            env = self.load_env()
            return "valid"

        self._gate(gates, "shared/.env configuration", envgate)
        db_revision = None
        if ok_target and env and current:
            def compatibility() -> str:
                nonlocal compat, db_revision
                state = self.db_state(current, env)
                db_revision = state.revision
                cur_release, target_release = self.load_release(current), self.load_release(target_id)
                # Union of both releases' graphs and ledgers: the target may know a
                # revision the current one does not (and vice versa).
                graph = {r["revision"]: r for r in [*target_release["alembic"]["revisions"], *cur_release["alembic"]["revisions"]]}
                ledger = {**self.load_ledger(target_id), **self.load_ledger(current)}
                ok, why = rc.rollback_compatibility(list(graph.values()), ledger, state.revision, target_release["alembic"]["head"])
                compat = (ok, why)
                if not ok:
                    raise rc.OpsError(rc.Exit.ROLLBACK_INCOMPATIBLE, f"the database cannot serve {target_id}: {why}")
                return why

            self._gate(gates, "database compatible with the rollback target (ledger)", compatibility)
        self.ctx.say(f"rollback{' (dry run)' if dry_run else ''}")
        self.show_gates(gates)
        failure = self.first_failure(gates)
        if failure:
            return failure.code
        if dry_run:
            self.ctx.say(f"  plan: current {current} -> {target_id}; previous {previous or 'none'} -> {current}; restart; verify health (forward-only, no downgrade)")
            self.ctx.say("(dry run: nothing was written, no service was touched)")
            return 0
        self.confirm(target_id)
        assert current and target_id
        dl.ensure_layout(self.layout)
        with dl.deploy_lock(self.layout):
            target_release = self.load_release(target_id)
            ev = dl.Evidence.create(self.layout, self.ctx.guard, self.ctx.clock(), target_release["git"]["commit"], "rollback")
            self.evidence = ev
            result = {"deploy_id": self.deploy_id, "kind": "manual-rollback", "source_release": current, "target_release": target_id,
                      "git_commit": target_release["git"]["commit"], "started_at": rc.utc_iso(self.ctx.clock()), "status": "in-progress",
                      "operator": dl.operator_name(), "tool_version": rc.TOOL_VERSION, "database_revision": db_revision,
                      "ledger_compatibility": compat[1] if compat else None, "downgrade": False}
            ev.write("result.json", result)
            self.log().event("rollback_start", command="rollback", source_release=current, target_release=target_id, detail=f"evidence {ev.directory.name}")
            unresolved = dl.read_activation_marker(self.layout)
            if unresolved:
                result["resolves_activation"] = {k: unresolved.get(k) for k in ("deploy_id", "from", "to", "deploy_type", "migrated")}
                ev.write("result.json", result)
            dl.write_activation_marker(self.layout, {"deploy_id": self.deploy_id, "from": current, "to": target_id, "started_at": rc.utc_iso(self.ctx.clock()), "evidence": ev.directory.name})
            old_previous = previous
            failure_text = ""
            checks: list[rp.Check] = []
            try:
                self._switch(target_id, current)
                self.ctx.service.restart()
                checks = self._wait_healthy(target_id)
                bad = [c for c in checks if not c.ok]
                failure_text = "; ".join(c.name for c in bad[:3])
            except rc.OpsError as exc:
                failure_text = exc.message
            ev.write("final-smoke.json", self._checks_evidence(checks, ok=not failure_text, port=self.ctx.prod_port, release=target_id, failure=failure_text))
            if not failure_text:
                dl.clear_activation_marker(self.layout)
                self.log().event("rollback_ok", command="rollback", source_release=current, target_release=target_id, exit_code=0)
                result.update({"status": "ok", "exit_code": 0, "finished_at": rc.utc_iso(self.ctx.clock())})
                ev.write("result.json", result)
                self.ctx.say(f"ROLLED BACK: current is now {target_id}; previous is {current}")
                return 0
            self.ctx.say(f"ROLLBACK FAILED HEALTH: {failure_text}. Returning to {current}.")
            code = self._auto_rollback(current, old_previous)
            self.log().event("rollback_failed", command="rollback", target_release=target_id, exit_code=code)
            result.update({"status": "failed", "exit_code": code, "finished_at": rc.utc_iso(self.ctx.clock())})
            ev.write("result.json", result)
            return code

    # -- resolve-activation ------------------------------------------------------------------------------------------------

    def cmd_resolve_activation(self, dry_run: bool) -> int:
        """Close an interrupted or failed activation (shared/state/activation.json)
        after a human has decided and verified the outcome. Changes no symlink
        and restarts nothing: it only records the decision and unblocks deploys."""
        self.require_tty(dry_run)
        self.check_root()
        marker = dl.read_activation_marker(self.layout)
        if marker is None:
            self.ctx.say("no unresolved activation")
            return 0
        current = self.current_id()
        self.ctx.say(f"unresolved activation: {json.dumps(marker, sort_keys=True)}")
        self.ctx.say(f"current -> {current or 'none'}; previous -> {self.previous_id() or 'none'}")
        checks = self._wait_healthy(current) if current else [rp.Check("a current release exists", False, "")]
        for check in checks:
            self.ctx.say(f"  [{'PASS' if check.ok else 'FAIL'}] {check.name}")
        if not all(c.ok for c in checks):
            raise rc.OpsError(rc.Exit.SMOKE, "the active release is not healthy; fix it (rollback / fix forward) before resolving")
        if dry_run:
            self.ctx.say("(dry run: the marker was left in place)")
            return 0
        self.confirm("resolve")
        with dl.deploy_lock(self.layout):
            self.log().event("activation_resolved", command="resolve-activation", source_release=marker.get("from"),
                             target_release=marker.get("to"), detail=f"current={current}", exit_code=0)
            dl.clear_activation_marker(self.layout)
        self.ctx.say("activation marker cleared; deploys are unblocked")
        return 0

    # -- restore-check ---------------------------------------------------------------------------------------------------

    def cmd_restore_check(self, dump: str, scratch_env: str | None, ephemeral: bool, candidate_release: str | None) -> int:
        self.check_root()
        if bool(scratch_env) == bool(ephemeral):
            raise rc.OpsError(rc.Exit.USAGE, "choose exactly one target: --ephemeral or --scratch-server-url-env VAR")
        env = self.load_env()
        current = self.current_id()
        probe = candidate_release or current
        if not probe:
            raise rc.OpsError(rc.Exit.PREFLIGHT, "there is no release to run the database probe with")
        meta = ddb.read_backup_meta(Path(dump))
        if ephemeral:
            state_major = int(meta.get("server_major") or 0)
            target = self.restore_target(state_major)
        else:
            scratch_url = self.ctx.environ.get(scratch_env or "", "")
            if not scratch_url:
                raise rc.OpsError(rc.Exit.CONFIG, f"environment variable {scratch_env} is not set")
            self.ctx.guard.add(scratch_url)
            target = ddb.ScratchServer(runner=self.ctx.runner, admin_url=scratch_url, production_url=env.values.get("DATABASE_URL"))
        dl.ensure_layout(self.layout)
        with dl.deploy_lock(self.layout):
            report = ddb.restore_check(
                runner=self.ctx.runner, dump=Path(dump), target=target, probe_release_dir=self.layout.release_dir(probe),
                app_env=env.values, migrate_release_dir=self.layout.release_dir(candidate_release) if candidate_release else None,
                expect_head=self.load_release(candidate_release)["alembic"]["head"] if candidate_release else None,
            )
            candidate = "skipped" if not candidate_release else ("ok" if report.migration_rehearsal == "ok" else "failed")
            ddb.record_rehearsal(self.layout, self.ctx.clock, report, candidate)
            self.log().event("restore_check", command="restore-check", alembic_from=report.alembic_revision, backup_sha256=report.dump_sha256,
                             checks=[f"target={report.target_kind}", f"counts_match={report.counts_match}", f"migration_rehearsal={report.migration_rehearsal}", f"destroyed={report.cleanup_ok}"],
                             exit_code=0 if report.ok else int(rc.Exit.BACKUP), rehearsal=True)
        for problem in report.problems:
            self.ctx.say(f"  problem: {problem}")
        if not report.ok:
            raise rc.OpsError(rc.Exit.BACKUP, "restore-check failed")
        self.ctx.say(f"restore-check OK ({report.target_kind}): alembic {report.alembic_revision or 'none'}, {report.tables} tables, row counts identical, "
                     f"migration rehearsal {report.migration_rehearsal}, disposable database destroyed")
        return 0

    # -- run (shared/.env wrapper, D18) -----------------------------------------------------------------------------------

    _ALEMBIC_READ_ONLY = ("current", "heads", "history", "check")

    def cmd_run(self, program: str, args: list[str]) -> int:
        """Run an allowlisted command in the *current* release's venv with the
        environment composed from shared/.env: nobody has to 'source' the
        file or paste DATABASE_URL into a shell. Output goes to the terminal
        only (never to a log). Alembic is read-only here: migrations only
        happen through 'deploy', after a verified backup."""
        self.check_root()
        current = self.current_id()
        if not current:
            raise rc.OpsError(rc.Exit.PREFLIGHT, "there is no current release")
        problems = self.prepared_problems(current)
        if problems:
            raise rc.OpsError(rc.Exit.PREFLIGHT, "current release failed verification: " + "; ".join(problems[:3]))
        if program == "alembic":
            if not args or args[0] not in self._ALEMBIC_READ_ONLY:
                raise rc.OpsError(rc.Exit.USAGE, "run alembic accepts only: " + ", ".join(self._ALEMBIC_READ_ONLY) + " (migrations go through 'deploy')")
            module = ["-m", "alembic"]
        elif program == "provision":
            module = ["-m", "app.cli.provision"]
        else:
            raise rc.OpsError(rc.Exit.USAGE, "run accepts: alembic, provision")
        env = self.load_env()
        directory = self.layout.release_dir(current)
        argv = [str(directory / "venv" / "bin" / "python"), *module, *args]
        self.log().event("run", command="run", source_release=current, detail=f"{program} {args[0] if args else ''}".strip()[:60])
        result = self.ctx.runner.run(argv, env=self.env_for_children(env), cwd=directory, timeout=3600, inherit_tty=True)
        return result.returncode

    # -- install-tools (bin/) -------------------------------------------------------------------------------------------------

    TOOL_FILES = ("artesa_deploy.py", "deploy_db.py", "deploy_layout.py", "release_artifact.py", "release_common.py", "release_probe.py")

    def cmd_install_tools(self, release_id: str, dry_run: bool) -> int:
        """Install this tool into <root>/bin from a verified, prepared release:
        bin/ops-<release-id>/ (read-only copy), bin/ops -> ops-<id> (atomic),
        bin/artesa-deploy (launcher) and bin/TOOL.json (provenance)."""
        self.require_tty(dry_run)
        self.check_root()
        rc.validate_release_id(release_id)
        problems = self.prepared_problems(release_id)
        if problems:
            raise rc.OpsError(rc.Exit.PREFLIGHT, "source release failed verification: " + "; ".join(problems[:3]))
        source = self.layout.release_dir(release_id) / "ops"
        launcher = source / "bin" / "artesa-deploy"
        missing = [n for n in self.TOOL_FILES if not (source / n).is_file()] + ([] if launcher.is_file() else ["bin/artesa-deploy"])
        if missing:
            raise rc.OpsError(rc.Exit.PREFLIGHT, "release does not carry the deploy tool: " + ", ".join(missing))
        release = self.load_release(release_id)
        self.ctx.say(f"install-tools from {release_id} (commit {release['git']['commit'][:12]}) into {self.layout.bin}")
        if dry_run:
            self.ctx.say("(dry run: nothing was written)")
            return 0
        self.confirm(release_id)
        dl.ensure_layout(self.layout)
        with dl.deploy_lock(self.layout):
            dest = self.layout.bin / f"ops-{release_id}"
            if not dest.exists():
                staging = self.layout.bin / f".ops-{release_id}.tmp-{os.getpid()}"
                os.mkdir(staging, 0o755)
                for name in self.TOOL_FILES:
                    shutil.copyfile(source / name, staging / name)
                    os.chmod(staging / name, 0o444)
                os.chmod(staging, 0o555)
                os.rename(staging, dest)
            tmp = self.layout.bin / f".ops.tmp-{os.getpid()}"
            os.symlink(dest.name, tmp)
            os.replace(tmp, self.layout.bin / "ops")
            tmp_launcher = self.layout.bin / f".artesa-deploy.tmp-{os.getpid()}"
            shutil.copyfile(launcher, tmp_launcher)
            os.chmod(tmp_launcher, 0o755)
            os.replace(tmp_launcher, self.layout.bin / "artesa-deploy")
            info = {"release_id": release_id, "git_commit": release["git"]["commit"], "tool_version": rc.TOOL_VERSION,
                    "installed_at": rc.utc_iso(self.ctx.clock()), "files": {n: rc.sha256_file(str(dest / n)) for n in self.TOOL_FILES}}
            tmp_info = self.layout.bin / f".TOOL.json.tmp-{os.getpid()}"
            tmp_info.write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="ascii")
            os.replace(tmp_info, self.layout.bin / "TOOL.json")
            self.log().event("install_tools", command="install-tools", target_release=release_id, git_sha=release["git"]["commit"][:12], exit_code=0)
        self.ctx.say(f"installed: {self.layout.bin / 'artesa-deploy'} -> ops-{release_id}")
        return 0

    # -- prune ---------------------------------------------------------------------------------------------------------------

    def cmd_prune(self, keep: int, delete: bool) -> int:
        self.check_root()
        protected: set[str] = set()
        marker = dl.read_activation_marker(self.layout)
        if marker:
            protected |= {v for v in (marker.get("from"), marker.get("to")) if isinstance(v, str)}
        if dl.lock_is_held(self.layout):
            raise rc.OpsError(rc.Exit.PREFLIGHT, "a deploy operation is in progress")
        plan = dl.plan_prune(self.layout, keep, protected)
        self.ctx.say(f"prune (keep {keep}){'' if delete else ' -- DRY RUN, nothing is deleted'}")
        self.ctx.say(f"  keep:    {', '.join(plan.keep) or '-'}")
        self.ctx.say(f"  delete:  {', '.join(plan.delete) or '-'}")
        for name, why in plan.refused:
            self.ctx.say(f"  refused: {name} ({why}) -- never deleted by this tool")
        if not delete or not plan.delete:
            return 0
        self.require_tty(False)
        self.confirm(f"delete {len(plan.delete)}")
        with dl.deploy_lock(self.layout):
            for release_id in plan.delete:
                dl.delete_release(self.layout, release_id)
                self.log().event("prune", command="prune", target_release=release_id, exit_code=0)
                self.ctx.say(f"  deleted {release_id}")
        return 0


# --- CLI ---------------------------------------------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="artesa-deploy", description="Immutable artifact releases for the ArtesaNFC backend (ADR-027).")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--rehearsal", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prod-port", type=int, default=rc.PRODUCTION_PORT, help=argparse.SUPPRESS)
    parser.add_argument("--candidate-port", type=int, default=rc.CANDIDATE_PORT, help=argparse.SUPPRESS)
    parser.add_argument("--pg-bindir", default=None, help="PostgreSQL server binaries for the restore-check (default /usr/lib/postgresql/<server major>/bin)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("status", help="show layout, releases, port and service state (read-only)")
    p.add_argument("--json", action="store_true")
    for name in ("prepare", "verify", "candidate", "deploy", "install-tools"):
        p = sub.add_parser(name)
        p.add_argument("release_id")
        if name in ("prepare", "deploy", "install-tools"):
            p.add_argument("--dry-run", action="store_true")
        if name == "verify":
            p.add_argument("--deep", action="store_true", help="also compare the venv with the lock")
        if name == "candidate":
            p.add_argument("--port", type=int, default=None)
        if name == "deploy":
            p.add_argument("--expect-commit", default=None, metavar="SHA", help="commit (12-40 hex) the operator expects, from origin/main / CI")
            p.add_argument("--allow-migration", action="store_true")
            p.add_argument("--no-auto-rollback", action="store_true")
            p.add_argument("--skip-public-check", action="store_true", help="skip the public edge check (logged)")
            p.add_argument("--keep-releases", type=int, default=5)
            p.add_argument("--restart-mode", choices=("sudo", "manual"), default="sudo")
    sub.add_parser("backup")
    p = sub.add_parser("rollback")
    p.add_argument("--to", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--restart-mode", choices=("sudo", "manual"), default="sudo")
    p = sub.add_parser("resolve-activation")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("restore-check")
    p.add_argument("dump")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--ephemeral", action="store_true", help="restore into a throw-away cluster (initdb) under shared/state")
    target.add_argument("--scratch-server-url-env", metavar="VAR", help="NAME of the environment variable holding a loopback scratch server URL")
    p.add_argument("--candidate-release", default=None, help="also rehearse this release's migrations on the restored copy")
    p = sub.add_parser("prune")
    p.add_argument("--dry-run", action="store_true", help="default behaviour; accepted for clarity")
    p.add_argument("--keep-releases", type=int, default=5)
    p.add_argument("--delete", action="store_true", help="actually delete (needs a TTY and a typed confirmation)")
    p = sub.add_parser("run", help="run 'alembic <read-only>' or 'provision ...' in current/ with shared/.env")
    p.add_argument("program", choices=("alembic", "provision"))
    p.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None, ctx: Context | None = None) -> int:
    args = build_parser().parse_args(argv)
    if ctx is None:
        root = Path(args.root) if args.root else Path(rc.DEFAULT_ROOT)
        ctx = Context(root=root, rehearsal=args.rehearsal, prod_port=args.prod_port, candidate_port=args.candidate_port,
                      pg_bindir=args.pg_bindir, restart_mode=getattr(args, "restart_mode", "sudo"))
    elif args.pg_bindir:
        ctx.pg_bindir = args.pg_bindir
    tool = Tool(ctx)
    try:
        if args.command == "status":
            return tool.cmd_status(args.json)
        if args.command == "prepare":
            return tool.cmd_prepare(args.release_id, args.dry_run)
        if args.command == "verify":
            return tool.cmd_verify(args.release_id, args.deep)
        if args.command == "candidate":
            port = args.port or ctx.candidate_port
            if port in rc.RESERVED_PORTS:
                raise rc.OpsError(rc.Exit.USAGE, f"port {port} is reserved (production {rc.PRODUCTION_PORT}, finanzas {rc.FINANZAS_PORT})")
            return tool.cmd_candidate(args.release_id, port)
        if args.command == "backup":
            return tool.cmd_backup()
        if args.command == "deploy":
            return tool.cmd_deploy(args.release_id, args.dry_run, args.allow_migration, not args.no_auto_rollback, args.skip_public_check,
                                   args.expect_commit, args.keep_releases)
        if args.command == "rollback":
            return tool.cmd_rollback(args.to, args.dry_run)
        if args.command == "resolve-activation":
            return tool.cmd_resolve_activation(args.dry_run)
        if args.command == "restore-check":
            return tool.cmd_restore_check(args.dump, args.scratch_server_url_env, args.ephemeral, args.candidate_release)
        if args.command == "prune":
            return tool.cmd_prune(args.keep_releases, args.delete)
        if args.command == "run":
            return tool.cmd_run(args.program, list(args.args))
        if args.command == "install-tools":
            return tool.cmd_install_tools(args.release_id, args.dry_run)
    except rc.OpsError as exc:
        ctx.say(f"error: {exc.message}")
        return exc.code
    except KeyboardInterrupt:
        ctx.say("interrupted")
        return int(rc.Exit.NO_TTY_OR_ABORT)
    except Exception as exc:  # noqa: BLE001 -- never print a traceback: messages/locals can carry secrets
        ctx.say(f"internal error: {type(exc).__name__} (details suppressed on purpose)")
        return int(rc.Exit.INTERNAL)
    return int(rc.Exit.USAGE)


if __name__ == "__main__":
    sys.exit(main())
