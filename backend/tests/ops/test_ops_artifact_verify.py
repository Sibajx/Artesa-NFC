"""Verification and safe extraction: nothing is extracted before the whole
archive has been validated; every tamper class is caught (N-08, ADR-027)."""
import gzip
import io
import os
import tarfile
from pathlib import Path

import pytest

import release_artifact as ra
import release_common as rc
from tests.ops.helpers import NOW, build, make_repo, rewrite_tar


@pytest.fixture()
def built(tmp_path):
    repo = make_repo(tmp_path)
    return build(repo, tmp_path / "incoming")


def expect_invalid(artifact: Path, match: str | None = None) -> None:
    with pytest.raises(rc.OpsError, match=match) as err:
        ra.verify_artifact(artifact)
    assert err.value.code == rc.Exit.ARTIFACT_INVALID


def test_a_good_artifact_verifies_and_extracts_identically(built, tmp_path):
    verified = ra.verify_artifact(built.artifact, expected_id=built.release_id)
    dest = tmp_path / "releases" / built.release_id
    dest.parent.mkdir()
    ra.extract_artifact(verified, dest)
    assert ra.verify_tree(dest) == []
    assert (dest / "app" / "main.py").read_bytes() == verified.contents["app/main.py"]


def test_sidecar_is_required_and_must_match(built):
    built.sidecar.unlink()
    expect_invalid(built.artifact, "sidecar")


def test_sidecar_checksum_mismatch(built):
    built.sidecar.write_text(f"{'0' * 64}  {built.artifact.name}\n")
    expect_invalid(built.artifact, "checksum")


def test_sidecar_naming_another_file_is_refused(built):
    built.sidecar.write_text(f"{rc.sha256_file(str(built.artifact))}  other.tar.gz\n")
    expect_invalid(built.artifact, "malformed or names a different")


def test_truncated_archive(built):
    data = built.artifact.read_bytes()
    built.artifact.write_bytes(data[: len(data) // 2])
    built.sidecar.write_text(f"{rc.sha256_file(str(built.artifact))}  {built.artifact.name}\n")  # sidecar is honest about the truncated bytes
    expect_invalid(built.artifact, "corrupt or truncated")


def test_truncated_archive_with_the_original_sidecar_fails_the_checksum(built):
    built.artifact.write_bytes(built.artifact.read_bytes()[:-40])
    expect_invalid(built.artifact, "checksum")


def test_wrong_requested_id(built):
    with pytest.raises(rc.OpsError, match="does not match the requested release id"):
        ra.verify_artifact(built.artifact, expected_id="20260101T000000Z-aaaaaaaaaaaa")


def test_file_name_must_carry_a_valid_release_id(built, tmp_path):
    bad = tmp_path / "artesa-nfc-latest.tar.gz"
    bad.write_bytes(built.artifact.read_bytes())
    bad.with_name(bad.name + ".sha256").write_text(f"{rc.sha256_file(str(bad))}  {bad.name}\n")
    expect_invalid(bad)


def test_symlinked_artifact_is_refused(built, tmp_path):
    link = tmp_path / "linkdir" / built.artifact.name
    link.parent.mkdir()
    link.symlink_to(built.artifact)
    expect_invalid(link, "not a regular file")


def test_tampered_file_content(built):
    rewrite_tar(built.artifact, lambda m: m.__setitem__("app/main.py", b"import os; os.system('x')\n"))
    expect_invalid(built.artifact, "does not match MANIFEST")


def test_added_file_not_in_manifest(built):
    rewrite_tar(built.artifact, lambda m: m.__setitem__("app/backdoor.py", b"x = 1\n"))
    expect_invalid(built.artifact, "disagree")


def test_deleted_file_still_in_manifest(built):
    rewrite_tar(built.artifact, lambda m: m.pop("app/core/db_safety.py"))
    expect_invalid(built.artifact, "disagree")


def test_tampered_manifest(built):
    def mutate(m):
        m["MANIFEST.sha256"] = m["MANIFEST.sha256"].replace(b"a", b"b", 1)
    rewrite_tar(built.artifact, mutate)
    expect_invalid(built.artifact)


def test_manifest_and_release_json_content_hash_must_agree(built):
    import json
    def mutate(m):
        rel = json.loads(m["RELEASE.json"]); rel["artifact"]["content_sha256"] = "0" * 64
        m["RELEASE.json"] = rc.canonical_json(rel)
    rewrite_tar(built.artifact, mutate)
    expect_invalid(built.artifact, "content_sha256")


def test_release_json_alembic_block_must_match_the_migration_files(built):
    import json
    def mutate(m):
        rel = json.loads(m["RELEASE.json"]); rel["alembic"]["revisions"] = rel["alembic"]["revisions"][:1]
        rel["alembic"]["head"] = "aaa111"
        m["RELEASE.json"] = rc.canonical_json(rel)
    rewrite_tar(built.artifact, mutate)
    expect_invalid(built.artifact, "alembic")


def test_release_id_inside_release_json_must_match_the_file_name(built):
    import json
    def mutate(m):
        rel = json.loads(m["RELEASE.json"]); rel["release_id"] = "20260101T000000Z-" + rel["release_id"][-12:]
        m["RELEASE.json"] = rc.canonical_json(rel)
    rewrite_tar(built.artifact, mutate)
    expect_invalid(built.artifact, "release_id")


def _hostile_tar(artifact: Path, add) -> None:
    """Valid tar plus one hostile member, with a matching sidecar."""
    import io as _io
    raw = _io.BytesIO()
    with tarfile.open(artifact, "r:gz") as src, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as out:
        for m in src:
            out.addfile(m, src.extractfile(m))
        add(out)
    artifact.write_bytes(raw.getvalue())
    artifact.with_name(artifact.name + ".sha256").write_text(f"{rc.sha256_bytes(raw.getvalue())}  {artifact.name}\n")


@pytest.mark.parametrize("name", ["../evil.py", "/etc/cron.d/evil", "app/../../evil.py", "app\\evil.py", "app//evil.py", "./app/x.py", "app/ evil.py", "app/é.py"])
def test_traversal_and_unsafe_names_are_refused_before_anything_is_written(built, name):
    def add(out):
        info = tarfile.TarInfo(name); info.size = 1
        out.addfile(info, io.BytesIO(b"x"))
    _hostile_tar(built.artifact, add)
    expect_invalid(built.artifact)


def test_symlink_member(built):
    def add(out):
        info = tarfile.TarInfo("app/link"); info.type = tarfile.SYMTYPE; info.linkname = "/etc/passwd"
        out.addfile(info)
    _hostile_tar(built.artifact, add)
    expect_invalid(built.artifact, "not a regular file")


def test_hardlink_member(built):
    def add(out):
        info = tarfile.TarInfo("app/hard"); info.type = tarfile.LNKTYPE; info.linkname = "app/main.py"
        out.addfile(info)
    _hostile_tar(built.artifact, add)
    expect_invalid(built.artifact, "not a regular file")


def test_directory_and_device_members(built):
    for kind in (tarfile.DIRTYPE, tarfile.CHRTYPE):
        def add(out, kind=kind):
            info = tarfile.TarInfo("app/dev"); info.type = kind
            out.addfile(info)
        _hostile_tar(built.artifact, add)
        expect_invalid(built.artifact, "not a regular file")


def test_duplicate_members(built):
    def add(out):
        info = tarfile.TarInfo("app/main.py"); info.size = 1; info.mode = 0o644
        out.addfile(info, io.BytesIO(b"x"))
    _hostile_tar(built.artifact, add)
    expect_invalid(built.artifact, "duplicate")


@pytest.mark.parametrize("mode", [0o4755, 0o666, 0o664, 0o000])
def test_unsafe_modes(built, mode):
    def add(out):
        info = tarfile.TarInfo("app/mode.py"); info.size = 1; info.mode = mode
        out.addfile(info, io.BytesIO(b"x"))
    _hostile_tar(built.artifact, add)
    expect_invalid(built.artifact, "unsafe mode")


@pytest.mark.parametrize("name", ["evil.py", "tests/test_x.py", "docs/x.md", "venv/x", ".git/config"])
def test_members_outside_the_allowlisted_roots(built, name):
    def add(out):
        info = tarfile.TarInfo(name); info.size = 1; info.mode = 0o644
        out.addfile(info, io.BytesIO(b"x"))
    _hostile_tar(built.artifact, add)
    expect_invalid(built.artifact)


@pytest.mark.parametrize("name", ["app/.env", "app/prod.pem", "ops/server.key", "app/__pycache__/x.cpython-314.pyc"])
def test_secret_shaped_members_are_refused(built, name):
    def add(out):
        info = tarfile.TarInfo(name); info.size = 1; info.mode = 0o644
        out.addfile(info, io.BytesIO(b"x"))
    _hostile_tar(built.artifact, add)
    expect_invalid(built.artifact, "forbidden file name|unsafe artifact path")


def test_nothing_is_extracted_when_validation_fails(built, tmp_path):
    rewrite_tar(built.artifact, lambda m: m.__setitem__("app/main.py", b"tampered\n"))
    root = tmp_path / "releases"
    root.mkdir()
    with pytest.raises(rc.OpsError):
        ra.verify_artifact(built.artifact)
    assert list(root.iterdir()) == []  # verification alone never touches the destination


def test_extract_refuses_to_overwrite_an_existing_directory(built, tmp_path):
    verified = ra.verify_artifact(built.artifact)
    dest = tmp_path / built.release_id
    dest.mkdir()
    with pytest.raises(FileExistsError):
        ra.extract_artifact(verified, dest)


def test_oversized_member_is_refused(built, monkeypatch):
    monkeypatch.setattr(rc, "MAX_FILE_BYTES", 10)
    expect_invalid(built.artifact, "too large")


def test_too_many_members(built, monkeypatch):
    monkeypatch.setattr(rc, "MAX_MEMBERS", 3)
    expect_invalid(built.artifact, "too many")


# --- drift on an extracted tree -------------------------------------------------------

@pytest.fixture()
def tree(built, tmp_path):
    verified = ra.verify_artifact(built.artifact)
    dest = tmp_path / "releases" / built.release_id
    dest.parent.mkdir()
    ra.extract_artifact(verified, dest)
    return dest


def test_drift_modified_file(tree):
    (tree / "app" / "core" / "config.py").write_text("# edited on the server\n")
    assert any("modified since extraction: app/core/config.py" in p for p in ra.verify_tree(tree))


def test_drift_missing_file(tree):
    os.remove(tree / "app" / "core" / "db_safety.py")
    assert any("missing file: app/core/db_safety.py" in p for p in ra.verify_tree(tree))


def test_drift_unrelated_main_py_in_the_release_root(tree):
    """The 2026-09-21 incident: an unrelated main.py (finanzas) in the backend root."""
    (tree / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI(title='other app')\n")
    assert any("unexpected file not in manifest: main.py" in p for p in ra.verify_tree(tree))


def test_drift_symlink_and_hardlink(tree):
    os.symlink("/etc/passwd", tree / "app" / "evil")
    os.link(tree / "app" / "main.py", tree / "app" / "hard.py")
    problems = " | ".join(ra.verify_tree(tree))
    assert "unexpected symlink: app/evil" in problems and "hardlink" in problems


def test_drift_group_writable(tree):
    os.chmod(tree / "app" / "main.py", 0o664)
    assert any("unsafe mode" in p for p in ra.verify_tree(tree))


def test_bytecode_and_venv_are_not_drift(tree):
    (tree / "app" / "__pycache__").mkdir()
    (tree / "app" / "__pycache__" / "main.cpython-314.pyc").write_bytes(b"\0")
    (tree / "venv" / "lib").mkdir(parents=True)
    (tree / "venv" / "lib" / "anything.py").write_text("x")
    (tree / ".prepared").write_text("{}")
    assert ra.verify_tree(tree) == []


def test_tampered_manifest_or_release_json_in_the_tree(tree):
    (tree / "RELEASE.json").write_text("{}")
    assert any("unreadable or invalid" in p for p in ra.verify_tree(tree))
