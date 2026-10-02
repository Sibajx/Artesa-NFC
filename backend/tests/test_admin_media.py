"""Gestión admin API, phase 4: media uploads and /media/ serving (docs/MEDIA.md)."""
from __future__ import annotations

import io
import os
import struct
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db_safety import UnsafeConfigurationError
from app.main import app
from app.models.audit_event import AuditEvent
from app.services import media as media_service
from tests.test_admin_api import auth, client, make_token, verifier  # noqa: F401  (fixtures)
from tests.test_admin_writes import H, act, new_artisan, new_piece

GPS_TAG = 0x8825
ORIENTATION_TAG = 0x0112


# --- fixtures and builders ------------------------------------------------------------


@pytest.fixture()
def media_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "media"
    (root / "originales").mkdir(parents=True)
    (root / "publico").mkdir()
    monkeypatch.setattr(get_settings(), "media_root", str(root))
    return root


def jpeg(size=(2400, 1800), color=(180, 40, 40), gps=True, orientation=None) -> bytes:
    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    if gps:
        exif[GPS_TAG] = {1: "N", 2: (17.0, 3.0, 36.0), 3: "W", 4: (96.0, 43.0, 12.0)}
    if orientation:
        exif[ORIENTATION_TAG] = orientation
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif, quality=90)
    return out.getvalue()


def png_rgba(size=(300, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, (0, 128, 0, 0)).save(out, "PNG")
    return out.getvalue()


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def trak(handler: bytes, width=640, height=360) -> bytes:
    tkhd = b"\0" * 76 + struct.pack(">II", width << 16, height << 16)
    hdlr = b"\0" * 8 + handler + b"\0" * 12 + b"track\0"
    return box(b"trak", box(b"tkhd", tkhd) + box(b"mdia", box(b"hdlr", hdlr)))


def mp4(audio=False, location=False, padding=100) -> bytes:
    mvhd = box(b"mvhd", b"\0" * 12 + struct.pack(">II", 1000, 2500) + b"\0" * 80)
    moov = mvhd + trak(b"vide")
    if audio:
        moov += trak(b"soun")
    if location:
        moov += box(b"udta", box(b"\xa9xyz", b"\0\x12\0\0+17.06-096.72/"))
    return box(b"ftyp", b"isom\0\0\0\0isomiso2") + box(b"moov", moov) + box(b"mdat", b"\0" * padding)


def glb(declared_length=None) -> bytes:
    body = b'{"asset":{"version":"2.0"}}   '
    json_chunk = struct.pack("<I4s", len(body), b"JSON") + body
    total = 12 + len(json_chunk)
    return b"glTF" + struct.pack("<II", 2, declared_length or total) + json_chunk


def upload(client, kind, record, data, role, alt="Máscara vista de frente", content_type="image/jpeg", **headers):
    params = {"role": role}
    if alt is not None:
        params["alt_text"] = alt
    return client.post(f"/api/admin/v1/{kind}/{record['id']}/media", params=params, content=data,
                       headers=H(**{"Content-Type": content_type, **headers}))


def files_under(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.fixture()
def piece(client):
    artisan = new_artisan(client, name="Taller Cuilápam")
    return new_piece(client, artisan["id"], name="Máscara de tigre")


# --- uploads: images -----------------------------------------------------------------


def test_photo_upload_publishes_a_clean_resized_jpeg_and_keeps_the_original(client, db_session, media_root, piece):
    original = jpeg()
    r = upload(client, "pieces", piece, original, "hero")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "active"
    assert body["media"] == {
        "type": "image", "role": "hero", "url": "/media/piezas/mascara-de-tigre/hero-01.jpg",
        "alt_text": "Máscara vista de frente", "position": 0,
        "format": {"width": 1600, "height": 1200, "mime_type": "image/jpeg"},
    }

    published = media_root / "publico/piezas/mascara-de-tigre/hero-01.jpg"
    with Image.open(published) as image:
        assert image.size == (1600, 1200)
        assert image.format == "JPEG"
        assert len(image.getexif()) == 0
        assert "exif" not in image.info
    assert b"PhoneMaker" not in published.read_bytes()

    originals = list((media_root / "originales/taller-cuilapam/mascara-de-tigre").iterdir())
    assert len(originals) == 1 and originals[0].suffix == ".jpg"
    assert originals[0].read_bytes() == original
    assert oct(originals[0].stat().st_mode & 0o777) == "0o600"

    event = db_session.execute(select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(body["id"]))).scalar_one()
    assert event.action == "media.uploaded" and event.entity_type == "media_asset"
    assert event.event_metadata["storage_path"] == "piezas/mascara-de-tigre/hero-01.jpg"
    assert event.event_metadata["owner_id"] == piece["id"]

    detail = client.get(f"/api/admin/v1/pieces/{piece['id']}", headers=auth(make_token())).json()
    assert [m["id"] for m in detail["media"]] == [body["id"]]


def test_uploads_are_numbered_and_never_overwrite(client, media_root, piece):
    folder = media_root / "publico/piezas/mascara-de-tigre"
    folder.mkdir(parents=True)
    (folder / "gallery-01.jpg").write_bytes(b"left by hand")
    first = upload(client, "pieces", piece, jpeg(size=(400, 300)), "gallery").json()
    second = upload(client, "pieces", piece, jpeg(size=(400, 300)), "gallery").json()
    assert first["media"]["url"].endswith("/gallery-02.jpg")
    assert second["media"]["url"].endswith("/gallery-03.jpg")
    assert (first["media"]["position"], second["media"]["position"]) == (0, 1)
    assert (folder / "gallery-01.jpg").read_bytes() == b"left by hand"


def test_exif_rotation_is_applied(client, media_root, piece):
    r = upload(client, "pieces", piece, jpeg(size=(800, 600), orientation=6), "detail")
    assert r.json()["media"]["format"]["width"] == 600
    assert r.json()["media"]["format"]["height"] == 800


def test_transparent_png_becomes_a_jpeg_and_keeps_its_original_extension(client, media_root, piece):
    r = upload(client, "pieces", piece, png_rgba(), "detail", content_type="image/png")
    assert r.status_code == 201, r.text
    assert r.json()["media"]["url"].endswith("/detail-01.jpg")
    assert [p.suffix for p in (media_root / "originales/taller-cuilapam/mascara-de-tigre").iterdir()] == [".png"]


def test_artisan_portrait_goes_to_the_artisan_folders(client, media_root):
    artisan = new_artisan(client, name="María López")
    r = upload(client, "artisans", artisan, jpeg(size=(300, 400)), "portrait", alt="Retrato de María")
    assert r.status_code == 201, r.text
    assert r.json()["media"]["url"] == "/media/artesanos/maria-lopez/portrait-01.jpg"
    assert len(list((media_root / "originales/maria-lopez/_artesano").iterdir())) == 1


# --- uploads: video and 3D -----------------------------------------------------------


def test_silent_mp4_without_location_is_published_as_is(client, media_root, piece):
    data = mp4()
    r = upload(client, "pieces", piece, data, "process", alt=None, content_type="video/mp4")
    assert r.status_code == 201, r.text
    assert r.json()["media"]["type"] == "video"
    assert r.json()["media"]["format"] == {"width": 640, "height": 360, "duration_seconds": 2.5, "mime_type": "video/mp4"}
    assert (media_root / "publico/piezas/mascara-de-tigre/process-01.mp4").read_bytes() == data


@pytest.mark.parametrize(("data", "code"), [
    (mp4(audio=True), "video_has_audio"),
    (mp4(location=True), "video_has_location"),
    (b"\0\0\0\x10ftypisom\0\0\0\0garbage", "invalid_video"),
])
def test_videos_with_sound_location_or_damage_are_refused(client, media_root, piece, data, code):
    r = upload(client, "pieces", piece, data, "process", alt=None, content_type="video/mp4")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == code
    assert files_under(media_root) == []


def test_video_over_4_mb_is_413(client, media_root, piece):
    r = upload(client, "pieces", piece, mp4(padding=4 * 1024 * 1024), "process", alt=None, content_type="video/mp4")
    assert r.status_code == 413
    assert files_under(media_root) == []


def test_glb_model(client, media_root, piece):
    r = upload(client, "pieces", piece, glb(), "model_3d", alt=None, content_type="model/gltf-binary")
    assert r.status_code == 201, r.text
    assert r.json()["media"]["url"].endswith("/model-01.glb")
    assert r.json()["media"]["format"] == {"format": "glb", "file_size_bytes": len(glb())}
    bad = upload(client, "pieces", piece, glb(declared_length=999), "model_3d", alt=None, content_type="model/gltf-binary")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_model"


# --- uploads: refusals ---------------------------------------------------------------


@pytest.mark.parametrize(("kind", "role", "data", "code"), [
    ("pieces", "portrait", jpeg(size=(10, 10)), "invalid_role"),
    ("artisans", "hero", jpeg(size=(10, 10)), "invalid_role"),
    ("pieces", "hero", glb(), "wrong_type_for_role"),
    ("pieces", "model_3d", jpeg(size=(10, 10)), "wrong_type_for_role"),
    ("pieces", "hero", b"GIF89a....", "unsupported_type"),
    ("pieces", "hero", b"%PDF-1.7 hello", "unsupported_type"),
])
def test_role_and_type_rules(client, media_root, kind, role, data, code):
    artisan = new_artisan(client, name="Taller Uno")
    record = artisan if kind == "artisans" else new_piece(client, artisan["id"], name="Pieza Uno")
    r = upload(client, kind, record, data, role)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == code
    assert files_under(media_root) == []


def test_photo_needs_alt_text(client, media_root, piece):
    for alt in (None, "   "):
        r = upload(client, "pieces", piece, jpeg(size=(10, 10)), "hero", alt=alt)
        assert r.status_code == 422 and r.json()["error"]["field"] == "alt_text"
    assert files_under(media_root) == []


def test_decompression_bomb_is_refused_before_decoding(client, media_root, piece):
    out = io.BytesIO()
    Image.new("1", (10000, 6000)).save(out, "PNG")
    r = upload(client, "pieces", piece, out.getvalue(), "hero", content_type="image/png")
    assert r.status_code == 422
    assert r.json()["error"]["code"] in ("image_too_large", "unsupported_type")


def test_declared_body_over_25_mb_is_413_without_reading_it(client, media_root, piece):
    r = client.post(f"/api/admin/v1/pieces/{piece['id']}/media", params={"role": "hero"}, content=b"x",
                    headers=H(**{"Content-Type": "image/jpeg", "Content-Length": str(26 * 1024 * 1024)}))
    assert r.status_code == 413


def test_streamed_body_over_the_limit_is_413(client, media_root, piece, monkeypatch):
    monkeypatch.setattr(media_service, "MAX_UPLOAD_BYTES", 1000)

    def chunks():
        for _ in range(5):
            yield b"x" * 400

    r = client.post(f"/api/admin/v1/pieces/{piece['id']}/media", params={"role": "hero"}, content=chunks(),
                    headers=H(**{"Content-Type": "image/jpeg"}))
    assert r.status_code == 413


@pytest.mark.parametrize(("headers", "status"), [
    ({"X-Artesa-Admin": None}, 403),
    ({"Origin": "https://evil.example"}, 403),
    ({"Content-Type": "text/plain"}, 415),
    ({"Content-Type": "multipart/form-data; boundary=x"}, 415),
    ({"Content-Type": "application/x-www-form-urlencoded"}, 415),
])
def test_upload_csrf_guard(client, media_root, piece, headers, status):
    base = H(**{"Content-Type": "image/jpeg"})
    for key, value in headers.items():
        if value is None:
            base.pop(key)
        else:
            base[key] = value
    r = client.post(f"/api/admin/v1/pieces/{piece['id']}/media", params={"role": "hero", "alt_text": "x"},
                    content=jpeg(size=(10, 10)), headers=base)
    assert r.status_code == status
    assert files_under(media_root) == []


def test_upload_without_identity_is_401(client, media_root, piece):
    headers = {"X-Artesa-Admin": "1", "Content-Type": "image/jpeg"}
    r = client.post(f"/api/admin/v1/pieces/{piece['id']}/media", params={"role": "hero"}, content=b"x", headers=headers)
    assert r.status_code == 401


def test_upload_is_503_when_media_is_not_configured(client, piece, monkeypatch):
    monkeypatch.setattr(get_settings(), "media_root", "")
    r = upload(client, "pieces", piece, jpeg(size=(10, 10)), "hero")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "media_not_configured"


def test_archived_owner_and_unknown_owner(client, media_root, piece):
    assert upload(client, "pieces", {"id": str(uuid.uuid4())}, jpeg(size=(10, 10)), "hero").status_code == 404
    archived = act(client, "pieces", piece, "archive").json()
    r = upload(client, "pieces", archived, jpeg(size=(10, 10)), "hero")
    assert r.status_code == 409 and r.json()["error"]["code"] == "archived"


def test_a_failed_insert_leaves_no_files(client, media_root, piece, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(media_service, "_audit", boom)
    with pytest.raises(RuntimeError):
        upload(client, "pieces", piece, jpeg(size=(10, 10)), "hero")
    assert files_under(media_root) == []


# --- edits and archive ---------------------------------------------------------------


def test_edit_alt_text_and_position_with_if_match(client, db_session, media_root, piece):
    asset = upload(client, "pieces", piece, jpeg(size=(10, 10)), "gallery").json()
    r = client.patch(f"/api/admin/v1/media/{asset['id']}", json={"alt_text": "Detalle del tallado", "position": 5},
                     headers=H(**{"If-Match": asset["updated_at"]}))
    assert r.status_code == 200, r.text
    assert (r.json()["media"]["alt_text"], r.json()["media"]["position"]) == ("Detalle del tallado", 5)
    stale = client.patch(f"/api/admin/v1/media/{asset['id']}", json={"position": 1},
                         headers=H(**{"If-Match": asset["updated_at"]}))
    assert stale.status_code == 412
    cleared = client.patch(f"/api/admin/v1/media/{asset['id']}", json={"alt_text": ""},
                           headers=H(**{"If-Match": r.json()["updated_at"]}))
    assert cleared.status_code == 422 and cleared.json()["error"]["code"] == "alt_text_required"
    assert client.patch(f"/api/admin/v1/media/{asset['id']}", json={"position": 1}, headers=H()).status_code == 428
    actions = db_session.execute(select(AuditEvent.action).where(AuditEvent.entity_id == uuid.UUID(asset["id"]))
                                 .order_by(AuditEvent.occurred_at)).scalars().all()
    assert actions == ["media.uploaded", "media.updated"]


def test_archive_hides_from_the_public_api_and_restore_brings_it_back(client, media_root, piece):
    asset = upload(client, "pieces", piece, jpeg(size=(10, 10)), "hero").json()
    artisan = client.get(f"/api/admin/v1/artisans/{piece['artisan']['id']}", headers=auth(make_token())).json()
    act(client, "artisans", artisan, "publish")
    act(client, "pieces", piece, "publish")
    public = TestClient(app).get("/api/v1/pieces/mascara-de-tigre").json()
    assert [m["url"] for m in public["media"]] == ["/media/piezas/mascara-de-tigre/hero-01.jpg"]

    archived = client.post(f"/api/admin/v1/media/{asset['id']}/archive", json={},
                           headers=H(**{"If-Match": asset["updated_at"]}))
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert TestClient(app).get("/api/v1/pieces/mascara-de-tigre").json()["media"] == []
    again = client.post(f"/api/admin/v1/media/{asset['id']}/archive", json={},
                        headers=H(**{"If-Match": archived.json()["updated_at"]}))
    assert again.status_code == 409

    restored = client.post(f"/api/admin/v1/media/{asset['id']}/restore", json={},
                           headers=H(**{"If-Match": archived.json()["updated_at"]}))
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    assert (media_root / "publico/piezas/mascara-de-tigre/hero-01.jpg").exists()


# --- /media/ serving -----------------------------------------------------------------


def test_published_file_is_served_immutable_with_open_cors(client, media_root, piece):
    upload(client, "pieces", piece, jpeg(size=(10, 10)), "hero")
    r = TestClient(app).get("/media/piezas/mascara-de-tigre/hero-01.jpg", headers={"Origin": "https://artesanfc.com"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["access-control-allow-origin"] == "*"
    assert r.content == (media_root / "publico/piezas/mascara-de-tigre/hero-01.jpg").read_bytes()
    head = TestClient(app).head("/media/piezas/mascara-de-tigre/hero-01.jpg")
    assert head.status_code == 200 and head.content == b""


@pytest.mark.parametrize("path", [
    "/media/piezas/mascara/../../originales/x/y.jpg",
    "/media/piezas/%2e%2e/%2e%2e/originales/secret.jpg",
    "/media/originales/taller/pieza/foto.jpg",
    "/media/piezas/mascara/Hero-01.jpg",
    "/media/piezas/mascara/.hidden.jpg",
    "/media/piezas/mascara/notes.txt",
    "/media/piezas/mascara/hero-01.jpg/",
    "/media/piezas/hero-01.jpg",
    "/media/",
    "/media/piezas/mascara/missing-01.jpg",
])
def test_only_well_formed_public_paths_are_served(media_root, path):
    (media_root / "originales/x").mkdir(parents=True)
    (media_root / "originales/x/y.jpg").write_bytes(b"private")
    folder = media_root / "publico/piezas/mascara"
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_bytes(b"x")
    (folder / ".hidden.jpg").write_bytes(b"x")
    (folder / "Hero-01.jpg").write_bytes(b"x")
    r = TestClient(app).get(path)
    assert r.status_code == 404
    assert b"private" not in r.content


def test_symlink_out_of_publico_is_not_followed(media_root):
    secret = media_root / "originales/secret.jpg"
    secret.write_bytes(b"private")
    folder = media_root / "publico/piezas/mascara"
    folder.mkdir(parents=True)
    os.symlink(secret, folder / "hero-01.jpg")
    assert TestClient(app).get("/media/piezas/mascara/hero-01.jpg").status_code == 404


def test_media_is_404_when_not_configured_and_405_for_writes(media_root, monkeypatch):
    folder = media_root / "publico/piezas/mascara"
    folder.mkdir(parents=True)
    (folder / "hero-01.jpg").write_bytes(b"x")
    assert TestClient(app).post("/media/piezas/mascara/hero-01.jpg").status_code == 405
    monkeypatch.setattr(get_settings(), "media_root", "")
    assert TestClient(app).get("/media/piezas/mascara/hero-01.jpg").status_code == 404


# --- configuration -------------------------------------------------------------------


def test_media_root_must_be_absolute_with_both_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MEDIA_ROOT", "relative/media")
    with pytest.raises(UnsafeConfigurationError, match="absolute"):
        Settings()
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    with pytest.raises(UnsafeConfigurationError, match="originales"):
        Settings()
    (tmp_path / "originales").mkdir()
    (tmp_path / "publico").mkdir()
    settings = Settings()
    assert settings.media_enabled and settings.media_public_dir == tmp_path / "publico"


@pytest.mark.parametrize("relative", [
    "../originales/x/y.jpg", "piezas/../../etc/passwd", "piezas/a/../b/c.jpg", "piezas/a/b.jpg/../c.jpg",
    "/etc/passwd", "piezas//b.jpg", "piezas/a/b.JPG", "piezas/a/b.jpg\x00.png", "piezas/a/b c.jpg",
])
def test_resolver_rejects_raw_traversal_shapes(media_root, relative):
    from app.core.media_files import resolve_media_path

    assert resolve_media_path(media_root / "publico", relative) is None


# --- Range header (Starlette 0.48 quadratic range merge, issue #121) ------------------


@pytest.fixture()
def video_file(media_root) -> bytes:
    folder = media_root / "publico/piezas/mascara"
    folder.mkdir(parents=True)
    data = bytes(range(256)) * 40
    (folder / "process-01.mp4").write_bytes(data)
    return data


@pytest.mark.parametrize(("header", "expected"), [
    ("bytes=0-99", slice(0, 100)),
    ("bytes=100-", slice(100, None)),
    ("bytes=-50", slice(-50, None)),
])
def test_a_single_simple_range_is_honoured(video_file, header, expected):
    r = TestClient(app).get("/media/piezas/mascara/process-01.mp4", headers={"Range": header})
    assert r.status_code == 206
    assert r.content == video_file[expected]
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("header", [
    ",".join(f"{i}-{i}" for i in range(0, 4000, 2)).join(["bytes=", ""]),
    "bytes=0-1,5-9",
    "bytes=0-1, 3-4",
    "items=0-10",
    "bytes=abc",
    "bytes=" + "9" * 30 + "-",
])
def test_any_other_range_is_ignored_and_the_whole_file_served(video_file, header):
    r = TestClient(app).get("/media/piezas/mascara/process-01.mp4", headers={"Range": header})
    assert r.status_code == 200
    assert r.content == video_file


# --- delete (never-public only) and role changes (2026-10) ---------------------------


def admin_piece(client, piece) -> dict:
    return client.get(f"/api/admin/v1/pieces/{piece['id']}", headers=auth(make_token())).json()


def delete(client, asset, **headers):
    return client.request("DELETE", f"/api/admin/v1/media/{asset['id']}", json={},
                          headers=H(**{"If-Match": asset["updated_at"], **headers}))


def test_never_public_media_is_deleted_with_its_files_and_its_number_stays_taken(client, db_session, media_root, piece):
    asset = upload(client, "pieces", piece, jpeg(size=(10, 10)), "gallery").json()
    assert asset["deletable"] is True
    assert admin_piece(client, piece)["media"][0]["deletable"] is True
    assert any(p.startswith("originales/") for p in files_under(media_root))

    r = delete(client, asset)
    assert r.status_code == 204, r.text
    assert admin_piece(client, piece)["media"] == []
    assert files_under(media_root) == ["publico/piezas/mascara-de-tigre/gallery-01.deleted"]
    actions = db_session.execute(select(AuditEvent.action).where(AuditEvent.entity_id == uuid.UUID(asset["id"]))
                                 .order_by(AuditEvent.occurred_at)).scalars().all()
    assert actions == ["media.uploaded", "media.deleted"]

    # The tombstone keeps "01" taken: a cached URL is never reused.
    again = upload(client, "pieces", piece, jpeg(size=(10, 10)), "gallery").json()
    assert again["media"]["url"].endswith("/gallery-02.jpg")


def test_media_of_a_published_record_can_only_be_archived(client, media_root, piece):
    artisan = client.get(f"/api/admin/v1/artisans/{piece['artisan']['id']}", headers=auth(make_token())).json()
    act(client, "artisans", artisan, "publish")
    act(client, "pieces", piece, "publish")
    asset = upload(client, "pieces", piece, jpeg(size=(10, 10)), "hero").json()
    assert asset["deletable"] is False
    r = delete(client, asset)
    assert r.status_code == 409 and r.json()["error"]["code"] == "may_have_been_public"
    assert (media_root / "publico/piezas/mascara-de-tigre/hero-01.jpg").exists()


def test_media_that_was_public_once_stays_archive_only(client, media_root, piece):
    asset = upload(client, "pieces", piece, jpeg(size=(10, 10)), "hero").json()
    artisan = client.get(f"/api/admin/v1/artisans/{piece['artisan']['id']}", headers=auth(make_token())).json()
    artisan = act(client, "artisans", artisan, "publish").json()
    published = act(client, "pieces", piece, "publish").json()
    act(client, "pieces", published, "unpublish")
    item = admin_piece(client, piece)["media"][0]
    assert item["deletable"] is False
    assert delete(client, item).status_code == 409


def test_a_draft_artisans_photo_is_deletable_and_delete_needs_the_version(client, media_root):
    artisan = new_artisan(client, name="Taller Prueba")
    asset = upload(client, "artisans", artisan, jpeg(size=(10, 10)), "portrait").json()
    assert asset["deletable"] is True
    edited = client.patch(f"/api/admin/v1/media/{asset['id']}", json={"position": 3},
                          headers=H(**{"If-Match": asset["updated_at"]})).json()
    assert delete(client, asset).status_code == 412
    assert client.request("DELETE", f"/api/admin/v1/media/{asset['id']}", json={}, headers=H()).status_code == 428
    assert delete(client, edited).status_code == 204
    assert delete(client, edited).status_code == 404


def test_role_can_change_within_the_owner_roles_and_file_type(client, db_session, media_root, piece):
    asset = upload(client, "pieces", piece, jpeg(size=(10, 10)), "gallery").json()
    r = client.patch(f"/api/admin/v1/media/{asset['id']}", json={"role": "hero"},
                     headers=H(**{"If-Match": asset["updated_at"]}))
    assert r.status_code == 200, r.text
    assert r.json()["media"]["role"] == "hero"
    for role, code in (("model_3d", "wrong_type_for_role"), ("portrait", "invalid_role")):
        bad = client.patch(f"/api/admin/v1/media/{asset['id']}", json={"role": role},
                           headers=H(**{"If-Match": r.json()["updated_at"]}))
        assert bad.status_code == 422 and bad.json()["error"]["code"] == code, (role, bad.text)
    changes = db_session.execute(select(AuditEvent.event_metadata).where(
        AuditEvent.entity_id == uuid.UUID(asset["id"]), AuditEvent.action == "media.updated")).scalar_one()
    assert changes == {"changes": {"role": {"from": "gallery", "to": "hero"}}}
