"""M3 (docs/MEDIA.md §5): off-host copies of the media originals (backup_media.py).

Same fakes as D10.1/D10.2: the fake age (XOR after a valid header) and the
directory-backed FakeStore. No network, no real key.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
from pathlib import Path

import pytest

import backup_media as bm
import backup_remote as br
import release_common as rc
from tests.ops.helpers import NOW, write_env
from tests.ops.test_backup_d10 import backups, layout, plaintext_anywhere, s, state  # noqa: F401 -- fixture reuse
from tests.ops.test_backup_d10_2 import bucket, remote_env, run

PHOTO = b"\xff\xd8\xff\xe0 original photo bytes " * 50
OTHER = b"\xff\xd8\xff\xe0 another photo " * 40


@pytest.fixture
def media(s) -> Path:
    root = s.root / "media"
    originals = root / "originales"
    (originals / "taller-uno" / "mascara-tigre").mkdir(parents=True)
    (originals / "taller-uno" / "_artesano").mkdir(parents=True)
    (root / "publico").mkdir()
    (originals / "taller-uno" / "mascara-tigre" / "20260929T100000Z-aaaa1111.jpg").write_bytes(PHOTO)
    (originals / "taller-uno" / "_artesano" / "20260929T100500Z-bbbb2222.jpg").write_bytes(OTHER)
    write_env(s.root, extra=f"MEDIA_ROOT={root}\n")
    return root


def decrypt_fake(data: bytes) -> bytes:
    body = data[data.index(b"\n--- ") + 1:]
    body = body[body.index(b"\n") + 1:]
    return bytes(b ^ 0x5A for b in body)


def bundle_members(backup: Path) -> dict[str, bytes]:
    plain = decrypt_fake((backup / "bundle.tar.age").read_bytes())
    with tarfile.open(fileobj=io.BytesIO(plain)) as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


def media_objects(s) -> list[Path]:
    root = bucket(s) / br.DEFAULT_PREFIX / "media" / "originales"
    return sorted(p for p in root.glob("*.age")) if root.exists() else []


def sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


# --- without MEDIA_ROOT / without remote --------------------------------------------------------


def test_without_media_root_nothing_changes(s):
    assert run(s, "run") == 0
    assert "MEDIA: NOT CONFIGURED" in s.sink.text
    assert state(s)["media"] == {"status": "not-configured"}
    members = bundle_members(backups(s)[0])
    assert bm.INDEX_NAME not in members
    assert json.loads(members["manifest.json"])["media"] == {"status": "not-configured (no MEDIA_ROOT)"}


def test_index_goes_inside_the_encrypted_bundle_even_without_remote(s, media):
    assert run(s, "run") == 0
    members = bundle_members(backups(s)[0])
    index = json.loads(members[bm.INDEX_NAME])
    assert index["files"] == [
        {"path": "taller-uno/_artesano/20260929T100500Z-bbbb2222.jpg", "size": len(OTHER), "sha256": sha(OTHER)},
        {"path": "taller-uno/mascara-tigre/20260929T100000Z-aaaa1111.jpg", "size": len(PHOTO), "sha256": sha(PHOTO)},
    ]
    assert json.loads(members["manifest.json"])["media"]["files"] == 2
    assert state(s)["media"]["status"] == "local-only"
    assert "NOT copied off-host" in s.sink.text
    # the index names files; it never sits in clear next to the backups
    assert b"mascara-tigre" not in (backups(s)[0] / "meta.json").read_bytes()


# --- off-host ------------------------------------------------------------------------------------


def test_originals_are_encrypted_uploaded_once_and_verified(s, media):
    remote_env(s)
    assert run(s, "run") == 0
    objects = media_objects(s)
    assert sorted(p.name for p in objects) == sorted(f"{sha(d)}.age" for d in (PHOTO, OTHER))
    for obj in objects:
        data = obj.read_bytes()
        assert data.startswith(b"age-encryption.org/v1\n") and b"original photo" not in data and b"another photo" not in data
        assert sha(decrypt_fake(data)) == obj.stem
    assert state(s)["media"]["status"] == "verified" and state(s)["media"]["uploaded_last_run"] == 2
    record = json.loads((layout(s).offsite_dir / bm.RECORD_NAME).read_text())
    assert {o["plain_sha256"] for o in record["objects"]} == {sha(PHOTO), sha(OTHER)}
    assert all(o["uploaded"] for o in record["objects"])
    assert "every original VERIFIED off-host (2 uploaded this run)" in s.sink.text
    assert list(layout(s).staging.iterdir()) == [] and plaintext_anywhere(s) == []


def test_second_run_uploads_only_new_contents_and_duplicates_once(s, media):
    remote_env(s)
    assert run(s, "run") == 0
    copy = media / "originales" / "taller-uno" / "mascara-tigre" / "20260929T110000Z-cccc3333.jpg"
    copy.write_bytes(PHOTO)                              # same bytes, another name
    new = media / "originales" / "taller-uno" / "mascara-tigre" / "20260929T120000Z-dddd4444.png"
    new.write_bytes(b"\x89PNG new original")
    assert run(s, "run", clock=lambda: NOW.replace(hour=5)) == 0
    assert len(media_objects(s)) == 3
    assert state(s)["media"]["uploaded_last_run"] == 1
    assert state(s)["media"]["files"] == 4 and state(s)["media"]["contents"] == 3


def test_hashes_are_cached_by_size_and_mtime(s, media, monkeypatch):
    remote_env(s)
    assert run(s, "run") == 0
    calls = []
    real = bm._sha256
    monkeypatch.setattr(bm, "_sha256", lambda p: calls.append(p) or real(p))
    assert run(s, "run", clock=lambda: NOW.replace(hour=6)) == 0
    assert calls == []


def test_symlinks_and_hidden_files_are_not_copied(s, media, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"not an original")
    os.symlink(outside, media / "originales" / "taller-uno" / "link.jpg")
    (media / "originales" / "taller-uno" / ".partial.jpg").write_bytes(b"half written")
    remote_env(s)
    assert run(s, "run") == 0
    assert len(media_objects(s)) == 2
    index = json.loads(bundle_members(backups(s)[0])[bm.INDEX_NAME])
    assert [f["path"] for f in index["files"]] == [
        "taller-uno/_artesano/20260929T100500Z-bbbb2222.jpg", "taller-uno/mascara-tigre/20260929T100000Z-aaaa1111.jpg"]


class MediaOutage(br.FakeStore):
    def put(self, name, path, sha1, sha256, content_type):
        if "/media/" in name:
            raise br.RemoteError(rc.Exit.BACKUP, "cannot reach the remote (fake outage)")
        return super().put(name, path, sha1, sha256, content_type)


def test_media_outage_keeps_the_database_backup_fails_the_run_and_is_retried(s, media):
    remote_env(s)
    assert run(s, "run", store=MediaOutage) == int(rc.Exit.BACKUP)
    assert state(s)["offsite"]["status"] == "verified"
    assert state(s)["media"]["status"] == "failed" and state(s)["media"]["consecutive_failures"] == 1
    assert "MEDIA OFFSITE: FAILED" in s.sink.text
    assert list(layout(s).staging.iterdir()) == [] and media_objects(s) == []
    assert run(s, "status") == int(rc.Exit.PREFLIGHT)
    assert run(s, "run", clock=lambda: NOW.replace(hour=7)) == 0
    assert len(media_objects(s)) == 2 and state(s)["media"]["status"] == "verified"


def test_missing_originales_fails_the_media_step_only(s, media):
    shutil.rmtree(media / "originales")
    assert run(s, "run") == int(rc.Exit.BACKUP)
    assert len(backups(s)) == 1
    assert state(s)["media"]["status"] == "failed"


def test_remote_check_relists_the_media_objects(s, media):
    remote_env(s)
    assert run(s, "run") == 0
    assert run(s, "remote-check") == 0
    assert "verified objects still present and identical" in s.sink.text
    media_objects(s)[0].unlink()
    assert run(s, "remote-check") == int(rc.Exit.BACKUP)
    assert "MISSING or CHANGED" in s.sink.text and "/media/originales/" in s.sink.text
