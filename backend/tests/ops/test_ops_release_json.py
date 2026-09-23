"""RELEASE.json: closed, versioned schema; no server-specific or secret data;
exactly one Alembic head (N-08, ADR-027)."""
import copy
import json

import pytest

import release_common as rc
from tests.ops.helpers import CANARY_PASSWORD, DATABASE_URL, build, make_repo, migration, run_git


@pytest.fixture()
def release(tmp_path):
    return build(make_repo(tmp_path), tmp_path / "o").release


def invalid(obj, **kw):
    with pytest.raises(rc.OpsError) as err:
        rc.validate_release_json(obj, **kw)
    assert err.value.code == rc.Exit.ARTIFACT_INVALID
    return err.value.message


def test_a_built_release_json_is_valid_and_has_the_approved_fields(release):
    assert rc.validate_release_json(release, expected_id=release["release_id"]) is release
    assert release["schema_version"] == 1
    assert set(release) == {"schema_version", "project", "release_id", "channel", "git", "build", "artifact", "deps", "alembic", "runtime"}
    assert set(release["git"]) == {"commit", "commit_short", "tree", "committed_at", "reachable_from"}
    assert set(release["build"]) == {"built_at_utc", "timestamp_source", "builder_version", "target_python"}
    assert set(release["artifact"]) == {"roots", "file_count", "content_sha256"}
    assert set(release["deps"]) == {"lockfile", "lockfile_sha256", "install_mode"}
    assert set(release["alembic"]) == {"head", "heads_count", "revisions"}
    assert release["build"]["target_python"] == "3.14"
    assert release["alembic"]["head"] == "bbb222" and release["alembic"]["heads_count"] == 1


@pytest.mark.parametrize("path", [("release_id",), ("git",), ("git", "commit"), ("build", "target_python"), ("artifact", "content_sha256"),
                                  ("deps", "lockfile_sha256"), ("alembic", "head"), ("alembic", "revisions"), ("runtime", "app_env_required"), ("channel",),
                                  ("project",), ("git", "commit_short"), ("build", "timestamp_source")])
def test_every_required_field_is_required(release, path):
    obj = copy.deepcopy(release)
    target = obj
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    assert "missing" in invalid(obj)


@pytest.mark.parametrize("section,key", [(None, "hostname"), (None, "ip_address"), (None, "username"), (None, "database_url"), (None, "env"),
                                        ("build", "builder_host"), ("runtime", "database_user"), ("git", "remote_url"), ("deps", "index_url")])
def test_unknown_or_server_specific_fields_are_refused(release, section, key):
    obj = copy.deepcopy(release)
    (obj if section is None else obj[section])[key] = "x"
    assert "unexpected field" in invalid(obj)


@pytest.mark.parametrize("value", [DATABASE_URL, "postgresql://u:p@h/db", "/home/energias/artesa-nfc", "10.0.0.5", "https://user:pw@host/x"])
def test_secret_or_private_values_are_refused_even_in_allowed_fields(release, value):
    obj = copy.deepcopy(release)
    obj["build"]["builder_version"] = value
    assert "credential-shaped" in invalid(obj)


def test_no_secret_from_the_environment_or_working_tree_reaches_release_json(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "backend" / ".env").write_text(f"DATABASE_URL={DATABASE_URL}\n")
    text = json.dumps(build(repo, tmp_path / "o").release)
    assert CANARY_PASSWORD not in text and "canary_db_operator" not in text and "127.0.0.1" not in text and "/home/" not in text


def test_heads_count_must_be_one(release):
    obj = copy.deepcopy(release)
    obj["alembic"]["heads_count"] = 2
    assert "exactly one head" in invalid(obj)


def test_head_must_match_the_revision_graph(release):
    obj = copy.deepcopy(release)
    obj["alembic"]["head"] = "aaa111"
    assert "head does not match" in invalid(obj)


def test_release_id_must_match_expected_and_commit(release):
    assert "does not match the artifact name" in invalid(release, expected_id="20260101T000000Z-aaaaaaaaaaaa")
    obj = copy.deepcopy(release)
    obj["git"]["commit"] = "0" * 40
    assert "git.commit" in invalid(obj)


def test_production_channel_must_be_reachable_and_rehearsal_must_not_claim_it(release):
    obj = copy.deepcopy(release); obj["git"]["reachable_from"] = None
    assert "reachable from origin/main" in invalid(obj)
    obj = copy.deepcopy(release); obj["channel"] = "rehearsal"
    assert "must not claim reachability" in invalid(obj)


def test_schema_version_is_enforced(release):
    obj = copy.deepcopy(release); obj["schema_version"] = 2
    assert "schema_version" in invalid(obj)


def test_two_alembic_heads_fail_the_build(tmp_path):
    repo = make_repo(tmp_path, {"backend/alembic/versions/ccc333_branch.py": migration("ccc333", "aaa111")})
    with pytest.raises(rc.OpsError, match="exactly one head"):
        build(repo, tmp_path / "o", ref="HEAD", rehearsal=True)


def test_release_json_is_canonical_ascii_json(release):
    data = rc.canonical_json(release)
    assert data.endswith(b"\n") and data.isascii()
    assert json.loads(data) == release
