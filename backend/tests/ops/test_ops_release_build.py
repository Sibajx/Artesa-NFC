"""Release builder: deterministic, allowlisted, built from git objects only,
and only from history reachable from origin/main (N-08, ADR-027)."""
import io
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest

import build_release as br
import release_artifact as ra
import release_common as rc
from tests.ops.helpers import NOW, build, commit, make_repo, run_git


def members(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, "r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar}


def test_same_commit_gives_the_same_logical_content_hash(tmp_path):
    repo = make_repo(tmp_path)
    a = build(repo, tmp_path / "a", when=NOW)
    b = build(repo, tmp_path / "b", when=NOW + timedelta(hours=5))  # different build time and id
    assert a.release["artifact"]["content_sha256"] == b.release["artifact"]["content_sha256"]
    assert a.manifest == b.manifest
    assert a.release_id != b.release_id
    # the archives themselves differ only through RELEASE.json (built_at/release_id)
    ma, mb = members(a.artifact), members(b.artifact)
    assert {k: v for k, v in ma.items() if k != "RELEASE.json"} == {k: v for k, v in mb.items() if k != "RELEASE.json"}


def test_same_commit_and_time_is_byte_for_byte_identical(tmp_path):
    repo = make_repo(tmp_path)
    a = build(repo, tmp_path / "a")
    b = build(repo, tmp_path / "b")
    assert a.artifact.read_bytes() == b.artifact.read_bytes()
    assert a.sidecar.read_text() == b.sidecar.read_text()


def test_tar_is_normalized(tmp_path):
    repo = make_repo(tmp_path)
    result = build(repo, tmp_path / "o")
    with tarfile.open(result.artifact, "r:gz") as tar:
        names = [m.name for m in tar]
        assert names == sorted(names)
        for m in tar:
            assert (m.uid, m.gid, m.uname, m.gname, m.mode) == (0, 0, "", "", 0o644)
            assert m.mtime == int(run_git(repo, "show", "-s", "--format=%ct", "HEAD"))
            assert m.isreg()
    assert result.artifact.read_bytes()[4:8] == b"\0\0\0\0"  # gzip mtime field zeroed


def test_only_allowlisted_roots_are_packaged(tmp_path):
    repo = make_repo(tmp_path)
    result = build(repo, tmp_path / "o")
    names = set(members(result.artifact))
    assert "app/main.py" in names and "alembic.ini" in names and "requirements-prod.lock" in names
    assert {"RELEASE.json", "MANIFEST.sha256"} <= names
    assert not any(n.startswith(("tests/", "docs/")) or n in (".env.example", "README.md") for n in names)
    assert {n.split("/")[0] for n in names} <= {"app", "alembic", "ops", "alembic.ini", "requirements-prod.lock", "RELEASE.json", "MANIFEST.sha256"}


def test_dirty_and_untracked_working_tree_cannot_contaminate(tmp_path):
    """Built from git objects: a real .env, an unrelated main.py and an edited
    tracked file in the working tree are invisible to the artifact (checked
    through the rehearsal-only --allow-dirty escape hatch)."""
    repo = make_repo(tmp_path)
    clean = build(repo, tmp_path / "clean", ref="HEAD", rehearsal=True)
    (repo / "backend" / ".env").write_text("DATABASE_URL=postgresql://u:PWCANARY@h/db\n")
    (repo / "backend" / "main.py").write_text("# unrelated finanzas main\n")
    (repo / "backend" / "app" / "extra_untracked.py").write_text("x = 1\n")
    (repo / "backend" / "app" / "main.py").write_text("# locally edited, uncommitted\n")
    (repo / "backend" / "app" / "__pycache__").mkdir()
    (repo / "backend" / "app" / "__pycache__" / "junk.pyc").write_bytes(b"\0")
    dirty = br.build_release(repo, tmp_path / "dirty", ref="HEAD", rehearsal=True, release_time=NOW, allow_dirty=True)
    assert dirty.manifest == clean.manifest
    blob = b"".join(members(dirty.artifact).values())
    assert b"PWCANARY" not in blob and b"unrelated finanzas" not in blob and b"locally edited" not in blob
    assert set(members(dirty.artifact)) == set(members(clean.artifact))


def test_modified_tracked_file_refuses_the_build(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "backend" / "app" / "main.py").write_text("# locally edited, uncommitted\n")
    for kwargs in ({}, {"ref": "HEAD", "rehearsal": True}):
        with pytest.raises(rc.OpsError) as err:
            build(repo, tmp_path / "o", **kwargs)
        assert err.value.code == rc.Exit.ARTIFACT_INVALID and "modified tracked files" in err.value.message
    assert not (tmp_path / "o").exists()


def test_allow_dirty_is_rehearsal_only(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(rc.OpsError) as err:
        br.build_release(repo, tmp_path / "o", allow_dirty=True)
    assert err.value.code == rc.Exit.USAGE


def test_untracked_files_do_not_block_the_build(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "notes.md").write_text("scratch\n")
    (repo / "backend" / ".env").write_text("APP_ENV=local\n")
    assert build(repo, tmp_path / "o").release["channel"] == rc.CHANNEL_PRODUCTION


@pytest.mark.parametrize("bad", ["backend/app/.env", "backend/app/.env.production", "backend/app/server.pem", "backend/ops/id.key", "backend/app/secret.p12"])
def test_secret_shaped_tracked_files_inside_the_roots_fail_the_build(tmp_path, bad):
    repo = make_repo(tmp_path, {bad: "SECRET=1\n"})
    with pytest.raises(rc.OpsError) as err:
        build(repo, tmp_path / "o", ref="HEAD", rehearsal=True)
    assert err.value.code == rc.Exit.ARTIFACT_INVALID


def test_tracked_symlink_inside_the_roots_fails_the_build(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "backend" / "app" / "evil").symlink_to("/etc/passwd")
    run_git(repo, "add", "-A"); run_git(repo, "commit", "-q", "-m", "symlink")
    with pytest.raises(rc.OpsError, match="non-regular"):
        build(repo, tmp_path / "o", ref="HEAD", rehearsal=True)


def test_ref_must_resolve_to_a_commit(tmp_path):
    repo = make_repo(tmp_path)
    for ref in ("no-such-ref", "--upload-pack=x", "HEAD; rm -rf /", ""):
        with pytest.raises(rc.OpsError) as err:
            build(repo, tmp_path / "o", ref=ref)
        assert err.value.code == rc.Exit.ARTIFACT_INVALID


def test_production_build_refuses_a_commit_not_reachable_from_origin_main(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, {"backend/app/extra.py": "x = 1\n"}, "unpushed")  # local only
    with pytest.raises(rc.OpsError, match="not reachable from origin/main"):
        build(repo, tmp_path / "o", ref="HEAD")
    run_git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, {"backend/app/feature.py": "x = 2\n"}, "feature")
    with pytest.raises(rc.OpsError, match="not reachable from origin/main"):
        build(repo, tmp_path / "o", ref="feature")


def test_local_main_does_not_count_only_the_remote_tracking_ref_does(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, {"backend/app/extra.py": "x = 1\n"}, "local main ahead")  # local main moved, origin/main did not
    with pytest.raises(rc.OpsError, match="not reachable"):
        build(repo, tmp_path / "o", ref="main")


def test_production_build_accepts_pushed_history_and_records_reachability(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, {"backend/app/extra.py": "x = 1\n"}, "pushed", push=True)
    result = build(repo, tmp_path / "o", ref="origin/main")
    assert result.release["channel"] == "production"
    assert result.release["git"]["reachable_from"] == "origin/main"
    assert result.release["git"]["commit"] == run_git(repo, "rev-parse", "origin/main")


def test_rehearsal_build_is_stamped_and_claims_no_reachability(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, {"backend/app/extra.py": "x = 1\n"}, "local")
    result = build(repo, tmp_path / "o", ref="HEAD", rehearsal=True)
    assert result.release["channel"] == "rehearsal"
    assert result.release["git"]["reachable_from"] is None


def test_missing_remote_tracking_ref_fails_closed(tmp_path):
    repo = make_repo(tmp_path, with_origin=False)
    with pytest.raises(rc.OpsError, match="git fetch"):
        build(repo, tmp_path / "o", ref="HEAD")


def test_build_never_overwrites_an_existing_release(tmp_path):
    repo = make_repo(tmp_path)
    build(repo, tmp_path / "o")
    with pytest.raises(FileExistsError):
        build(repo, tmp_path / "o")


@pytest.mark.parametrize("missing", ["backend/app/core/db_safety.py", "backend/requirements-prod.lock", "backend/ops/migration-classes.json", "backend/alembic.ini"])
def test_required_files_must_exist_in_the_commit(tmp_path, missing):
    repo = make_repo(tmp_path)
    run_git(repo, "rm", "-q", missing); run_git(repo, "commit", "-q", "-m", "drop")
    with pytest.raises(rc.OpsError, match="required file"):
        build(repo, tmp_path / "o", ref="HEAD", rehearsal=True)


def test_release_id_is_utc_timestamp_plus_sha12(tmp_path):
    repo = make_repo(tmp_path)
    result = build(repo, tmp_path / "o")
    assert result.release_id == f"20260921T030000Z-{run_git(repo, 'rev-parse', 'HEAD')[:12]}"
    assert result.artifact.name == f"artesa-nfc-{result.release_id}.tar.gz"
    assert result.sidecar.read_text() == f"{rc.sha256_bytes(result.artifact.read_bytes())}  {result.artifact.name}\n"


def test_runtime_expectations_are_derived_from_the_source(tmp_path):
    repo = make_repo(tmp_path)
    assert build(repo, tmp_path / "a").release["runtime"] == {"app_env_required": "production", "docs_in_production": False, "resolve_body_limit_bytes": 1024}
    old = make_repo(tmp_path / "old_world", {"backend/app/core/config.py": "class S:\n    pass\n", "backend/app/main.py": "x = 1\n"})
    rt = build(old, tmp_path / "b").release["runtime"]
    assert rt["docs_in_production"] is True and rt["resolve_body_limit_bytes"] is None


def test_default_build_time_is_the_commit_time_so_rebuilds_are_identical(tmp_path):
    """No --build-time / SOURCE_DATE_EPOCH: the release id and built_at_utc
    come from the commit, so two independent builds are byte-identical."""
    repo = make_repo(tmp_path)
    a = br.build_release(repo, tmp_path / "a")
    b = br.build_release(repo, tmp_path / "b")
    assert a.artifact.read_bytes() == b.artifact.read_bytes()
    assert a.release_id == b.release_id
    committed = run_git(repo, "show", "-s", "--format=%cI", "HEAD")
    assert a.release["build"]["timestamp_source"] == "commit"
    assert a.release["build"]["built_at_utc"] == a.release["git"]["committed_at"]
    assert committed.startswith(a.release["git"]["committed_at"][:19])


def test_git_tags_never_change_the_artifact_bytes(tmp_path, capsys):
    """Tagging the commit (D7: tag before the rollout), retagging or deleting
    the tag must not change the artifact: tags are not in RELEASE.json. The
    builder only prints them, as information."""
    repo = make_repo(tmp_path)
    untagged = br.build_release(repo, tmp_path / "a")
    run_git(repo, "tag", "v1.0.0")
    run_git(repo, "tag", "-a", "v1.0.0-annotated", "-m", "release")
    tagged = br.build_release(repo, tmp_path / "b")
    run_git(repo, "tag", "-d", "v1.0.0", "v1.0.0-annotated")
    removed = br.build_release(repo, tmp_path / "c")
    assert untagged.artifact.read_bytes() == tagged.artifact.read_bytes() == removed.artifact.read_bytes()
    assert untagged.sidecar.read_bytes() == tagged.sidecar.read_bytes() == removed.sidecar.read_bytes()
    assert "tag" not in tagged.release["git"] and b"v1.0.0" not in tagged.artifact.read_bytes()
    run_git(repo, "tag", "v2.0.0")
    assert br.main(["--repo", str(repo), "--out", str(tmp_path / "d")]) == 0
    assert "tags            v2.0.0  (informational, not in the artifact)" in capsys.readouterr().out


def test_source_date_epoch_is_honoured_and_validated(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
    assert br.main(["--repo", str(repo), "--out", str(tmp_path / "o")]) == 0
    out = capsys.readouterr().out
    assert "release_id      20260921T141320Z-" in out and "artifact_sha256 " in out
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "yesterday")
    assert br.main(["--repo", str(repo), "--out", str(tmp_path / "p")]) == rc.Exit.USAGE


def test_release_json_carries_project_and_short_commit(tmp_path):
    repo = make_repo(tmp_path)
    result = build(repo, tmp_path / "o")
    rel = result.release
    assert rel["project"] == "artesa-nfc"
    assert rel["git"]["commit_short"] == rel["git"]["commit"][:12] and rel["release_id"].endswith(rel["git"]["commit_short"])
    assert rel["build"]["builder_version"] == rc.BUILDER_VERSION and rel["build"]["target_python"] == "3.14"


@pytest.mark.parametrize("bad", [
    "backend/app/venv/lib.py", "backend/app/.venv/x.py", "backend/app/models.py~", "backend/app/x.py.orig",
    "backend/app/x.swp", "backend/ops/notes.tmp", "backend/app/site-packages/y.py", "backend/app/x.bak",
])
def test_temp_and_venv_files_inside_the_roots_fail_the_build(tmp_path, bad):
    repo = make_repo(tmp_path, {bad: "x = 1\n"})
    with pytest.raises(rc.OpsError) as err:
        build(repo, tmp_path / "o", ref="HEAD", rehearsal=True)
    assert err.value.code == rc.Exit.ARTIFACT_INVALID


def test_private_key_content_fails_the_build_whatever_the_file_name(tmp_path):
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
    repo = make_repo(tmp_path, {"backend/app/innocent.py": f'KEY = """{key}"""\n'})
    with pytest.raises(rc.OpsError, match="private key"):
        build(repo, tmp_path / "o", ref="HEAD", rehearsal=True)
