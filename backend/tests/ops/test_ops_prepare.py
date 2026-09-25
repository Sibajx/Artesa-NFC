"""prepare: verify -> stage -> per-release venv -> hashed install -> checks ->
marker only on success (N-08, ADR-027)."""
import json
import os
from pathlib import Path

import pytest

import deploy_layout as dl
import release_artifact as ra
import release_common as rc
from tests.ops.helpers import CANARY_PASSWORD, DATABASE_URL, Scenario, build, make_repo


class Prep:
    """Handlers that make FakeRunner behave like a working (or broken) venv."""

    def __init__(self, sc, *, pip_ok=True, import_ok=True, missing=(), extra=(), compile_ok=True, check_ok=True):
        self.sc, self.pip_ok, self.import_ok, self.missing, self.extra = sc, pip_ok, import_ok, set(missing), extra
        self.compile_ok, self.check_ok = compile_ok, check_ok
        self.calls = []

    def __call__(self, argv, env, cwd):
        self.calls.append((argv, env, cwd))
        if argv[1:3] == ["-m", "venv"]:
            venv = Path(argv[3]); (venv / "bin").mkdir(parents=True); (venv / "bin" / "python").write_text("#!/bin/sh\n"); (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
            return __import__("release_probe").RunResult(0)
        rp = __import__("release_probe")
        if "pip" in argv and "install" in argv:
            return rp.RunResult(0 if self.pip_ok else 1, "", "" if self.pip_ok else "ERROR: THESE PACKAGES DO NOT MATCH THE HASHES")
        if "pip" in argv and "list" in argv:
            lock = rc.parse_lock((Path(argv[0]).parents[2] / rc.LOCK_PATH).read_text())
            rows = [{"name": n, "version": v} for n, v in lock.items() if n not in self.missing] + [{"name": "pip", "version": "25"}]
            rows += [{"name": e, "version": "1"} for e in self.extra]
            return rp.RunResult(0, json.dumps(rows))
        if "pip" in argv and "check" in argv:
            return rp.RunResult(0 if self.check_ok else 1)
        if argv[1] == "-B" and "compile(" in argv[3]:
            return rp.RunResult(0 if self.compile_ok else 1, "", "")
        if argv[1] == "-B" and argv[3] == "import app.main":
            return rp.RunResult(0 if self.import_ok else 1, "", "" if self.import_ok else f"ModuleNotFoundError: No module named 'sqlalchemy'")
        if argv[1] == "-c" and "sys.version" in argv[2]:
            return rp.RunResult(0, "3.14.4\n")
        return None


@pytest.fixture()
def sc(tmp_path):
    s = Scenario(tmp_path)
    s.release("r1"); s.activate("r1")
    s.release("r2", prepared=False)
    (s.root / "releases" / s.ids["r2"]).rename(s.root / "elsewhere")  # keep only the artifact in incoming/
    return s


def prepare(sc, prep, *args, **kw):
    ctx = sc.ctx(**kw)
    ctx.runner.handlers = [prep]
    ctx.python = "python-not-sys"
    ctx.python_minor = None
    import artesa_deploy as ad
    orig = ad.Tool._python_minor
    ad.Tool._python_minor = lambda self: "3.14"
    try:
        return ad.main(["--root", str(sc.root), "--rehearsal", "prepare", sc.ids["r2"], *args], ctx)
    finally:
        ad.Tool._python_minor = orig


def state(sc):
    return sc.current(), sc.previous()


def test_prepare_success_writes_the_marker_last_and_leaves_current_alone(sc):
    prep = Prep(sc)
    assert prepare(sc, prep) == 0
    rel = sc.root / "releases" / sc.ids["r2"]
    marker = dl.read_prepared(rel)
    assert marker and marker["content_sha256"] == json.loads((rel / "RELEASE.json").read_text())["artifact"]["content_sha256"]
    assert ra.verify_tree(rel) == [] and (rel / "venv" / "bin" / "python").exists()
    assert state(sc) == (sc.ids["r1"], None)
    assert not [p for p in (sc.root / "releases").iterdir() if p.name.startswith(".staging")]


def test_prepare_installs_hashed_wheels_only_without_the_app_secrets(sc):
    prep = Prep(sc)
    prepare(sc, prep)
    install = next((a, e) for a, e, c in prep.calls if "install" in a)
    argv, env = install
    for flag in ("--require-hashes", "--only-binary=:all:", "--no-deps", "--no-cache-dir"):
        assert flag in argv
    assert "DATABASE_URL" not in env and "APP_ENV" not in env  # pip needs no configuration or secrets
    assert argv[0].endswith(f"releases/{sc.ids['r2']}/venv/bin/python")  # venv created at its FINAL path


def test_import_check_gets_the_env_but_never_in_argv(sc):
    prep = Prep(sc)
    prepare(sc, prep)
    argv, env, cwd = next(c for c in prep.calls if c[0][-1] == "import app.main")
    assert env["DATABASE_URL"] == DATABASE_URL and env["APP_ENV"] == "production"
    assert CANARY_PASSWORD not in " ".join(argv) and Path(cwd).name == sc.ids["r2"]


def test_no_venv_is_shared_between_releases(sc):
    prepare(sc, Prep(sc))
    v1, v2 = (sc.root / "releases" / sc.ids[n] / "venv" for n in ("r1", "r2"))
    assert os.path.realpath(v1) != os.path.realpath(v2) and not v2.is_symlink()


@pytest.mark.parametrize("prep_kwargs,needle", [
    ({"pip_ok": False}, "dependency install failed"),
    ({"import_ok": False}, "import app.main failed"),
    ({"missing": {"sqlalchemy"}}, "do not match the lock"),          # incident: shared venv missing SQLAlchemy
    ({"extra": {"leftpad"}}, "do not match the lock"),
    ({"compile_ok": False}, "does not compile"),
    ({"check_ok": False}, "pip check"),
])
def test_failed_prepare_leaves_no_trace(sc, prep_kwargs, needle):
    before = state(sc)
    assert prepare(sc, Prep(sc, **prep_kwargs)) == rc.Exit.PREPARE
    assert needle in sc.sink.text
    assert not (sc.root / "releases" / sc.ids["r2"]).exists()      # never left half-prepared
    assert not [p for p in (sc.root / "releases").iterdir() if p.name.startswith(".staging")]
    assert state(sc) == before
    assert sc.world.calls == []                                     # candidate never ran
    assert [e["event"] for e in sc.log_events()][-1] == "prepare_failed"


def test_import_error_output_never_leaks_the_database_url(sc):
    class Leaky(Prep):
        def __call__(self, argv, env, cwd):
            if argv[-1] == "import app.main":
                return __import__("release_probe").RunResult(1, "", f"RuntimeError: cannot connect to {DATABASE_URL}")
            return super().__call__(argv, env, cwd)
    assert prepare(sc, Leaky(sc)) == rc.Exit.PREPARE
    assert CANARY_PASSWORD not in sc.sink.text and "***" in sc.sink.text
    assert CANARY_PASSWORD not in (sc.root / "shared" / "state" / "deploy-log.jsonl").read_text()


def test_prepare_refuses_a_corrupt_artifact_before_creating_anything(sc):
    art = sc.artifacts["r2"]; art.write_bytes(art.read_bytes()[:-30])
    assert prepare(sc, Prep(sc)) == rc.Exit.ARTIFACT_INVALID
    assert list((sc.root / "releases").iterdir()) == [sc.root / "releases" / sc.ids["r1"]]


def test_prepare_is_idempotent_for_a_clean_prepared_release(sc):
    assert prepare(sc, Prep(sc)) == 0
    prep = Prep(sc)
    assert prepare(sc, prep) == 0 and "already prepared" in sc.sink.text
    assert prep.calls == []


def test_prepare_refuses_an_existing_unprepared_directory(sc):
    (sc.root / "releases" / sc.ids["r2"]).mkdir()
    assert prepare(sc, Prep(sc)) == rc.Exit.PREPARE and "remove it by hand" in sc.sink.text
    assert (sc.root / "releases" / sc.ids["r2"]).is_dir()  # not deleted: it was not created by this run


def test_prepare_needs_a_tty_but_not_for_dry_run(sc):
    assert prepare(sc, Prep(sc), tty=False) == rc.Exit.NO_TTY_OR_ABORT
    assert not (sc.root / "releases" / sc.ids["r2"]).exists()
    prep = Prep(sc)
    assert prepare(sc, prep, "--dry-run", tty=False) == 0
    assert prep.calls == [] and not (sc.root / "releases" / sc.ids["r2"]).exists()
    assert "nothing was written" in sc.sink.text


def test_prepare_requires_a_valid_env_for_the_import_check(sc):
    (sc.root / "shared" / ".env").chmod(0o644)
    assert prepare(sc, Prep(sc)) == rc.Exit.CONFIG
    assert not (sc.root / "releases" / sc.ids["r2"]).exists()


def test_prepare_refuses_a_host_python_that_is_not_the_release_target(sc):
    import artesa_deploy as ad
    ctx = sc.ctx(); ctx.runner.handlers = [Prep(sc)]
    orig = ad.Tool._python_minor; ad.Tool._python_minor = lambda self: "3.12"
    try:
        assert ad.main(["--root", str(sc.root), "--rehearsal", "prepare", sc.ids["r2"]], ctx) == rc.Exit.PREPARE
    finally:
        ad.Tool._python_minor = orig
    assert "targets 3.14" in sc.sink.text


def _strip_required(artifact: Path) -> None:
    """Rewrite the artifact without app/core/db_safety.py but with a
    self-consistent manifest, RELEASE.json and sidecar: only the required-file
    rule can object."""
    from tests.ops.helpers import rewrite_tar
    def mutate(m):
        m.pop("app/core/db_safety.py")
        manifest = rc.parse_manifest(m["MANIFEST.sha256"]); manifest.pop("app/core/db_safety.py")
        m["MANIFEST.sha256"] = rc.render_manifest(manifest)
        rel = json.loads(m["RELEASE.json"]); rel["artifact"]["file_count"] = len(manifest); rel["artifact"]["content_sha256"] = rc.sha256_bytes(m["MANIFEST.sha256"])
        m["RELEASE.json"] = rc.canonical_json(rel)
    rewrite_tar(artifact, mutate)


def test_incomplete_app_missing_db_safety_is_refused(sc):
    _strip_required(sc.artifacts["r2"])
    assert prepare(sc, Prep(sc)) == rc.Exit.ARTIFACT_INVALID and "required file missing" in sc.sink.text


def test_rehearsal_artifact_cannot_be_used_on_a_production_root(sc):
    import artesa_deploy as ad
    tool = ad.Tool(sc.ctx()); tool.ctx.rehearsal = False
    with pytest.raises(rc.OpsError) as err:
        tool.check_channel({"channel": "rehearsal"})
    assert err.value.code == rc.Exit.ARTIFACT_INVALID
