"""R4 / #131: install-tools is target-driven and auditable.

On 2026-09-27 the R2 tool installed R3's tool: bin/ops switched, but the
whole run (file list, modes, TOOL.json) was governed by R2's code. These
tests pin the new contract: the file set comes from the target MANIFEST, an
existing bin/ops-<id> is verified, TOOL.json v2 records the installer that
really ran, and the canonical path runs the target's own tool.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import artesa_deploy as ad
import release_common as rc
from tests.ops.helpers import Scenario

OPS_DIR = Path(__file__).resolve().parents[2] / "ops"
LAUNCHER = OPS_DIR / "bin" / "artesa-deploy"
TOOL_NAMES = sorted(ad.tool_files({p.name: "" for p in OPS_DIR.iterdir() if p.is_file()}))
TOOL_SOURCES = {f"backend/ops/{n}": (OPS_DIR / n).read_text() for n in TOOL_NAMES}
TOOLS = {**TOOL_SOURCES, "backend/ops/bin/artesa-deploy": LAUNCHER.read_text(),
         "backend/ops/build_release.py": (OPS_DIR / "build_release.py").read_text()}
ARGS = ["--rehearsal", "--prod-port", "18000", "--candidate-port", "18001"]


@pytest.fixture
def s(tmp_path):
    return Scenario(tmp_path)


def install(s, rid, *, executor=None, answers=None, dry_run=False, tty=True):
    ctx = s.ctx(answers=[rid] if answers is None else answers, tty=tty)
    if executor is not None:
        ctx.executor = executor
    s.last_ctx = ctx
    return ad.main(["--root", str(s.root), *ARGS, "install-tools", rid, *(["--dry-run"] if dry_run else [])], ctx)


def tool_json(s) -> dict:
    return json.loads((s.root / "bin" / "TOOL.json").read_text())


def release_ops(s, rid) -> Path:
    return s.root / "releases" / rid / "ops"


def commit_of(s, rid) -> str:
    return json.loads((s.root / "releases" / rid / "RELEASE.json").read_text())["git"]["commit"]


def writable(path: Path) -> None:
    os.chmod(path, 0o755)
    for entry in path.iterdir():
        os.chmod(entry, 0o644)


def test_tool_version_is_1_2_0():
    assert rc.TOOL_VERSION == "1.2.0"
    assert ad.target_tool_version(OPS_DIR) == "1.2.0"


def test_tool_files_rule():
    entries = {"artesa_deploy.py": "a", "new_module.py": "b", "build_release.py": "c", "migration-classes.json": "d",
               "bin/artesa-deploy": "e", "systemd/x.service": "f", "pkg/inner.py": "g"}
    assert ad.tool_files(entries) == {"artesa_deploy.py": "a", "new_module.py": "b"}


# --- installer identity / TOOL.json v2 -----------------------------------------------------------------------------

def test_install_from_the_target_release_is_the_canonical_path(s):
    rid = s.release("tools", TOOLS)
    me = release_ops(s, rid) / "artesa_deploy.py"
    assert install(s, rid, executor=me) == 0
    info = tool_json(s)
    assert set(info) == {"schema_version", "release_id", "git_commit", "tool_version", "installed_at", "files", "installer"}
    assert info["schema_version"] == 2 and info["release_id"] == rid and info["git_commit"] == commit_of(s, rid)
    assert info["tool_version"] == "1.2.0"
    assert set(info["files"]) == set(TOOL_NAMES) and "build_release.py" not in info["files"]
    inst = info["installer"]
    assert set(inst) == {"path", "release_id", "git_commit", "artesa_deploy_sha256", "tool_version", "matches_target"}
    assert inst["path"] == str(me.resolve()) and inst["release_id"] == rid and inst["git_commit"] == commit_of(s, rid)
    assert inst["artesa_deploy_sha256"] == rc.sha256_file(str(me)) and inst["tool_version"] == rc.TOOL_VERSION
    assert inst["matches_target"] is True
    assert "WARNING" not in s.sink.text
    assert stat.S_IMODE((s.root / "bin" / "TOOL.json").stat().st_mode) == 0o644
    event = [e for e in s.log_events() if e["event"] == "install_tools"][-1]
    assert f"installer={rid}" in event["detail"] and "matches_target=true" in event["detail"]


def test_installer_equal_to_target_from_another_location_matches(s):
    rid = s.release("tools", TOOLS)
    assert install(s, rid) == 0  # the repository's ops/ == the target's tool
    inst = tool_json(s)["installer"]
    assert inst["matches_target"] is True and inst["release_id"] is None and inst["path"] == str((OPS_DIR / "artesa_deploy.py").resolve())


def test_installer_different_from_target_warns_and_shows_the_canonical_command(s):
    changed = {**TOOLS, "backend/ops/release_probe.py": TOOL_SOURCES["backend/ops/release_probe.py"] + "\n# target-only change\n"}
    rid = s.release("tools", changed)
    assert install(s, rid) == 0
    info = tool_json(s)
    assert info["installer"]["matches_target"] is False
    assert "WARNING: the running installer is not the target's tool" in s.sink.text
    canonical = f"/usr/bin/python3 -I -B {s.root / 'releases' / rid}/ops/artesa_deploy.py --root {s.root} --rehearsal install-tools {rid}"
    assert canonical in s.sink.text
    # the installed files are the TARGET's, whatever the installer
    assert info["files"]["release_probe.py"] == rc.sha256_file(str(release_ops(s, rid) / "release_probe.py"))
    assert "matches_target=false" in [e for e in s.log_events() if e["event"] == "install_tools"][-1]["detail"]


def test_installer_running_from_bin_ops_is_identified(s):
    first = s.release("tools", TOOLS)
    assert install(s, first) == 0
    second = s.release("tools2", {"backend/app/extra.py": "X = 1\n"})
    running = s.root / "bin" / f"ops-{first}" / "artesa_deploy.py"
    assert install(s, second, executor=running) == 0
    inst = tool_json(s)["installer"]
    assert inst["release_id"] == first and inst["git_commit"] == commit_of(s, first) and inst["matches_target"] is True


def test_target_with_a_new_ops_module_installs_it(s):
    """The latent bug: an older installer with a hard-coded file list would
    omit this module and leave a tool that cannot import."""
    rid = s.release("tools", {**TOOLS, "backend/ops/restart_helper.py": "HELPER = True\n"})
    assert install(s, rid) == 0
    installed = s.root / "bin" / f"ops-{rid}"
    assert (installed / "restart_helper.py").read_text() == "HELPER = True\n"
    assert "restart_helper.py" in tool_json(s)["files"]
    assert not (installed / "build_release.py").exists()
    assert tool_json(s)["installer"]["matches_target"] is False  # the running tool does not have that module


def test_canonical_command_runs_from_releases_ops_in_a_real_interpreter(s):
    rid = s.release("tools", TOOLS)
    proc = subprocess.run([sys.executable, "-I", "-B", str(release_ops(s, rid) / "artesa_deploy.py"), "--root", str(s.root), "--rehearsal",
                           "install-tools", rid, "--dry-run"], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"(release {rid}, tool 1.2.0); matches target: yes" in proc.stdout
    assert "dry run: nothing was written" in proc.stdout and not (s.root / "bin" / "ops").exists()


# --- existing bin/ops-<id> ---------------------------------------------------------------------------------------------

def test_reinstall_of_an_identical_directory_is_idempotent(s):
    rid = s.release("tools", TOOLS)
    assert install(s, rid) == 0
    first = tool_json(s)
    assert install(s, rid) == 0
    assert "present and identical (reused)" in s.sink.text
    assert tool_json(s) == first  # same clock, same content


@pytest.mark.parametrize("damage, expected", [
    (lambda d: os.unlink(d / "deploy_db.py"), "missing file: deploy_db.py"),
    (lambda d: (d / "release_probe.py").write_text("# hot patch\n"), "content differs from the target release: release_probe.py"),
    (lambda d: (d / "extra.py").write_text("X = 1\n"), "unexpected entry: extra.py"),
])
def test_existing_directory_with_drift_is_refused_and_left_in_place(s, damage, expected):
    rid = s.release("tools", TOOLS)
    assert install(s, rid) == 0
    before = tool_json(s)
    installed = s.root / "bin" / f"ops-{rid}"
    writable(installed)
    damage(installed)
    for entry in installed.iterdir():
        os.chmod(entry, 0o444)
    os.chmod(installed, 0o555)
    assert install(s, rid) == rc.Exit.PREFLIGHT
    assert expected in s.sink.text and "it is NOT reused" in s.sink.text
    assert installed.is_dir() and tool_json(s) == before  # nothing deleted, nothing rewritten
    assert install(s, rid, dry_run=True, tty=False) == rc.Exit.PREFLIGHT  # the dry run reports it too


def test_existing_directory_with_a_writable_mode_is_refused(s):
    rid = s.release("tools", TOOLS)
    assert install(s, rid) == 0
    os.chmod(s.root / "bin" / f"ops-{rid}", 0o755)
    assert install(s, rid) == rc.Exit.PREFLIGHT and "directory mode 755" in s.sink.text


# --- atomicity and failure modes -----------------------------------------------------------------------------------

def test_failure_while_staging_leaves_the_active_tool_untouched(s, monkeypatch):
    a = s.release("a", TOOLS)
    assert install(s, a) == 0
    b = s.release("b", {"backend/app/b.py": "B = 1\n"})
    real, calls = ad.shutil.copyfile, {"n": 0}

    def flaky(src, dst, *args, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(src, dst, *args, **kw)

    monkeypatch.setattr(ad.shutil, "copyfile", flaky)
    assert install(s, b) == rc.Exit.INTERNAL
    assert os.readlink(s.root / "bin" / "ops") == f"ops-{a}" and tool_json(s)["release_id"] == a
    assert not (s.root / "bin" / f"ops-{b}").exists()
    assert not [p for p in (s.root / "bin").iterdir() if p.name.startswith(".")]  # own staging cleaned up


def test_failure_between_swap_and_tool_json_is_detected_and_repaired(s, monkeypatch):
    a = s.release("a", TOOLS)
    assert install(s, a) == 0
    b = s.release("b", {"backend/app/b.py": "B = 1\n"})

    def crash(self, info):
        (self.layout.bin / f".TOOL.json.tmp-{os.getpid()}").write_text("{}")
        raise OSError("power cut")

    monkeypatch.setattr(ad.Tool, "_write_tool_json", crash)
    assert install(s, b) == rc.Exit.INTERNAL
    assert os.readlink(s.root / "bin" / "ops") == f"ops-{b}" and tool_json(s)["release_id"] == a
    assert s.run(["status"]) == 0
    assert f"TOOLING WARNING: bin/ops -> ops-{b} but TOOL.json describes ops-{a} (partial install?)" in s.sink.text
    monkeypatch.undo()
    assert install(s, b) == 0
    assert "removed leftovers of an interrupted install" in s.sink.text and "previous installation was inconsistent" in s.sink.text
    assert tool_json(s)["release_id"] == b
    assert s.run(["status"]) == 0 and "TOOLING WARNING" not in s.sink.text


def test_stale_staging_directories_are_removed_under_the_lock(s):
    rid = s.release("tools", TOOLS)
    stale = s.root / "bin" / f".ops-{rid}.tmp-424242"
    stale.mkdir(parents=True)
    (stale / "half.py").write_text("x")
    os.chmod(stale / "half.py", 0o444); os.chmod(stale, 0o555)
    assert install(s, rid) == 0
    assert not stale.exists() and f".ops-{rid}.tmp-424242" in s.sink.text


def test_dry_run_writes_nothing(s):
    rid = s.release("tools", TOOLS)
    before = s.world.state_hash()
    assert install(s, rid, dry_run=True, tty=False) == 0
    assert s.world.state_hash() == before
    assert "tool files from the target MANIFEST" in s.sink.text and "absent (will be staged)" in s.sink.text


# --- status ------------------------------------------------------------------------------------------------------------

def test_status_reports_a_consistent_v2_installation(s):
    rid = s.release("tools", TOOLS)
    assert install(s, rid) == 0
    assert s.run(["status", "--json"]) == 0
    tooling = json.loads(s.sink.text)["tooling"]
    assert tooling == {"installed": True, "ops": f"ops-{rid}", "release_id": rid, "schema_version": 2,
                       "installer_matches_target": True, "problems": []}


def test_status_accepts_a_v1_tool_json(s):
    rid = s.release("tools", TOOLS)
    assert install(s, rid) == 0
    info = tool_json(s)
    v1 = {k: info[k] for k in ("release_id", "git_commit", "tool_version", "installed_at", "files")}
    (s.root / "bin" / "TOOL.json").write_text(json.dumps(v1))
    os.chmod(s.root / "bin" / "TOOL.json", 0o644)
    assert s.run(["status"]) == 0
    assert "(schema 1)" in s.sink.text and "n/a (TOOL.json v1)" in s.sink.text and "TOOLING WARNING" not in s.sink.text


def test_status_warns_about_a_modified_installed_file_and_a_loose_mode(s):
    rid = s.release("tools", TOOLS)
    assert install(s, rid) == 0
    installed = s.root / "bin" / f"ops-{rid}"
    writable(installed)
    (installed / "deploy_db.py").write_text("# drift\n")
    os.chmod(s.root / "bin" / "TOOL.json", 0o664)
    assert s.run(["status"]) == 0  # warnings, not a failure: status never blocks or repairs
    assert f"bin/ops-{rid}/deploy_db.py differs from TOOL.json" in s.sink.text
    assert "bin/TOOL.json mode 664 (expected 644)" in s.sink.text


def test_status_without_installed_tool_is_silent(s):
    s.release("r1"); s.activate("r1")
    assert s.run(["status", "--json"]) == 0
    assert json.loads(s.sink.text)["tooling"]["installed"] is False
