"""Secret canaries: no secret may reach stdout/stderr, logs, argv, artifacts,
manifests, RELEASE.json, the deploy log, backups' sidecars or error text
(N-08, ADR-027). Secrets MAY live in a child's environment."""
import json
import os
from pathlib import Path

import pytest

import deploy_db as ddb
import release_common as rc
import release_probe as rp
from tests.ops.helpers import CANARY_OTHER, CANARY_PASSWORD, CANARY_USER, DATABASE_URL, FakeRunner, Scenario, migration_files

SECRETS = [CANARY_PASSWORD, CANARY_USER, DATABASE_URL, CANARY_OTHER, "another-secret-value-123"]


def everything_under(root: Path) -> str:
    chunks = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.name != ".env" and path.stat().st_size < 5_000_000:
            try:
                chunks.append(path.read_bytes().decode("utf-8", "replace"))
            except OSError:
                pass
    return "\n".join(chunks)


@pytest.fixture()
def full_run(tmp_path):
    """prepare-equivalent releases, a migration deploy (with backup), a
    rollback and a status, all with canary-laden configuration."""
    sc = Scenario(tmp_path)
    sc.release("r1"); sc.activate("r1")
    sc.release("r_add", migration_files("ccc333", "bbb222", "additive"))
    sc.record_rehearsal()
    outputs = []
    assert sc.run(["deploy", sc.ids["r_add"], "--allow-migration"], answers=[sc.ids["r_add"]]) == 0
    outputs.append(sc.sink.text)
    assert sc.run(["rollback"], answers=[sc.ids["r1"]]) == 0
    outputs.append(sc.sink.text)
    sc.run(["status", "--json"], tty=False); outputs.append(sc.sink.text)
    sc.run(["status"], tty=False); outputs.append(sc.sink.text)
    sc.run(["deploy", sc.ids["r_add"], "--dry-run"], tty=False); outputs.append(sc.sink.text)
    sc.stdout = "\n".join(outputs)
    return sc


def test_no_secret_in_any_operator_visible_output(full_run):
    for secret in SECRETS:
        assert secret not in full_run.stdout


def test_no_secret_in_any_argv_ever_run(full_run):
    flat = "\n".join(" ".join(a) for a in full_run.world.argv_log)
    for secret in SECRETS:
        assert secret not in flat
    assert len(full_run.world.argv_log) >= 8   # the run really exercised the tool


def test_no_secret_in_any_file_the_tool_wrote_or_any_release_artifact(full_run):
    text = everything_under(full_run.root)
    for secret in SECRETS:
        assert secret not in text
    for p in (full_run.root / "releases").glob("*/RELEASE.json"):
        assert "DATABASE_URL" not in p.read_text()
    assert all(secret not in a.read_bytes().decode("latin1") for a in (full_run.root / "incoming").glob("*.tar.gz") for secret in SECRETS)


def test_children_receive_only_what_they_need_in_their_environment(full_run):
    """DATABASE_URL may be in the env of the app-touching children; the .env
    extras (Cloudflare token, SECRET_KEY) are never passed to anything."""
    holders, others = set(), 0
    for argv, env in zip(full_run.world.argv_log, full_run.world.envs_log):
        assert "CLOUDFLARE_API_TOKEN" not in env and "SECRET_KEY" not in env and CANARY_OTHER not in env.values()
        if env.get("DATABASE_URL"):
            holders.add(os.path.basename(argv[0]) if os.path.basename(argv[0]) != "python" else "python:" + (argv[1] if argv[1].startswith("-") else os.path.basename(argv[1])) + (":" + argv[2] if argv[1] == "-m" else ""))
        else:
            others += 1
    assert holders and all(h.startswith("python:") for h in holders), holders     # only release-venv children
    pg = [(argv, env) for argv, env in zip(full_run.world.argv_log, full_run.world.envs_log) if argv[0] in ("pg_dump", "pg_restore")]
    assert pg and all("DATABASE_URL" not in e for _, e in pg)
    # pg_dump reads production; the restore goes to the disposable cluster with its own one-off password
    assert all(e["PGPASSWORD"] == CANARY_PASSWORD for a, e in pg if a[0] == "pg_dump" and "--version" not in a)
    assert all(e.get("PGPASSWORD") != CANARY_PASSWORD for a, e in pg if a[0] == "pg_restore" and "--list" not in a)


def test_ops_errors_scrub_secrets_from_child_stderr(tmp_path):
    sc = Scenario(tmp_path); sc.release("r1"); sc.activate("r1"); sc.release("r_add", migration_files("ccc333", "bbb222", "additive")); sc.record_rehearsal()
    ctx = sc.ctx(answers=[sc.ids["r_add"]])
    def alembic_fails(argv, env, cwd):
        if "alembic" in argv and env.get("DATABASE_URL") == DATABASE_URL:  # production only; the rehearsal on the copy passes
            return rp.RunResult(1, "", f"sqlalchemy.exc.OperationalError: could not connect to {DATABASE_URL} as {CANARY_USER} pw {CANARY_PASSWORD}")
    ctx.runner.handlers = [alembic_fails]
    import artesa_deploy as ad
    code = ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "deploy", sc.ids["r_add"], "--allow-migration"], ctx)
    assert code == rc.Exit.MIGRATION_FAILED
    for secret in (CANARY_PASSWORD, DATABASE_URL):
        assert secret not in sc.sink.text and secret not in everything_under(sc.root)
    assert "***" in sc.sink.text


def test_the_guard_refuses_a_tool_bug_that_would_put_a_secret_in_argv(tmp_path):
    """Negative control for the boundary itself."""
    sc = Scenario(tmp_path); sc.release("r1"); sc.activate("r1")
    class Sabotaged(FakeRunner):
        def run(self, argv, **kw):
            if argv and argv[0] == "pg_dump" and "--version" not in argv:
                argv = [*argv, f"--dbname={DATABASE_URL}"]
            return super().run(argv, **kw)
    ctx = sc.ctx(); sabotaged = Sabotaged(sc.world, ctx.guard); ctx.runner = sabotaged
    import artesa_deploy as ad
    code = ad.main(["--root", str(sc.root), "--rehearsal", "--prod-port", "18000", "backup"], ctx)
    assert code == rc.Exit.INTERNAL and "secret value would appear in argv" in sc.sink.text
    assert DATABASE_URL not in sc.sink.text
    assert not any(a[0] == "pg_dump" and "--version" not in a for a in sc.world.argv_log)
    assert list((sc.root / "shared" / "backups").iterdir()) == []


def test_candidate_report_and_log_never_contain_secrets_even_when_the_app_would_print_them(tmp_path):
    from tests.ops.test_ops_candidate_process import candidate, release_dir  # noqa: F401
    root = tmp_path / "rel"
    from tests.ops import test_ops_candidate_process as tcp
    (root / "app").mkdir(parents=True); (root / "app" / "__init__.py").write_text(""); (root / "app" / "main.py").write_text(tcp.APP)
    (root / "venv" / "bin").mkdir(parents=True)
    import sys
    w = root / "venv" / "bin" / "python"; w.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n'); w.chmod(0o755)
    port, report = tcp.candidate(root, tmp_path, {"TEST_LEAK": DATABASE_URL})
    assert not report.ok and not report.log_clean                       # the leak is DETECTED...
    assert CANARY_PASSWORD not in json.dumps([c.__dict__ for c in report.checks])   # ...without echoing it


def test_release_json_and_manifest_of_a_real_build_contain_no_canary(tmp_path):
    from tests.ops.helpers import build, make_repo
    repo = make_repo(tmp_path)
    (repo / "backend" / ".env").write_text(f"DATABASE_URL={DATABASE_URL}\nSECRET_KEY={CANARY_OTHER}\n")
    result = build(repo, tmp_path / "o")
    blob = json.dumps(result.release) + result.manifest.decode() + result.sidecar.read_text()
    for secret in SECRETS:
        assert secret not in blob
