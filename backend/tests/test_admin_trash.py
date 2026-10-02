"""Gestión Papelera (2026-10): trash, restore and purge artisans and pieces,
and the replayed "ever public" rule for media."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.audit_event import AuditEvent
from tests.test_admin_api import auth, client, make_token, verifier  # noqa: F401  (fixtures)
from tests.test_admin_media import files_under, jpeg, media_root, upload  # noqa: F401  (fixture)
from tests.test_admin_writes import H, act, new_artisan, new_piece


def get(client, kind, record) -> dict:
    return client.get(f"/api/admin/v1/{kind}/{record['id']}", headers=auth(make_token())).json()


def post(client, kind, record, action):
    return client.post(f"/api/admin/v1/{kind}/{record['id']}/{action}", json={},
                       headers=H(**{"If-Match": record["updated_at"]}))


def names(client, kind, **params) -> list[str]:
    body = client.get(f"/api/admin/v1/{kind}", params=params, headers=auth(make_token())).json()
    return [r.get("full_name") or r.get("name") for r in body["data"]]


def test_photo_of_an_archived_never_published_artisan_is_deletable(client, media_root):
    # The PO's "holaaa": uploaded to a draft artisan that was then archived.
    artisan = new_artisan(client, name="Taller Prueba")
    asset = upload(client, "artisans", artisan, jpeg(size=(10, 10)), "gallery", alt="holaaa").json()
    act(client, "artisans", artisan, "archive", reason="prueba")
    item = get(client, "artisans", artisan)["media"][0]
    assert item["deletable"] is True
    r = client.request("DELETE", f"/api/admin/v1/media/{asset['id']}", json={},
                       headers=H(**{"If-Match": item["updated_at"]}))
    assert r.status_code == 204, r.text


def test_photo_stays_undeletable_once_its_artisan_was_published(client, media_root):
    artisan = new_artisan(client, name="Taller Publicado")
    upload(client, "artisans", artisan, jpeg(size=(10, 10)), "portrait")
    published = act(client, "artisans", artisan, "publish").json()
    act(client, "artisans", published, "unpublish")
    assert get(client, "artisans", artisan)["media"][0]["deletable"] is False


def test_trash_restore_and_lists(client):
    artisan = new_artisan(client, name="Ana Ruiz")
    piece = new_piece(client, artisan["id"], name="Máscara de jaguar")
    r = post(client, "pieces", piece, "trash")
    assert r.status_code == 200, r.text
    trashed = r.json()
    assert trashed["trashed_at"] and trashed["purge_blocker"] is None
    assert "Máscara de jaguar" not in names(client, "pieces")
    assert names(client, "pieces", trashed="true") == ["Máscara de jaguar"]
    assert get(client, "artisans", artisan)["pieces"][0]["trashed_at"]

    # While trashed, every ordinary write is refused.
    edit = client.patch(f"/api/admin/v1/pieces/{piece['id']}", json={"name": "Otra"},
                        headers=H(**{"If-Match": trashed["updated_at"]}))
    assert edit.status_code == 409 and edit.json()["error"]["code"] == "trashed"
    assert post(client, "pieces", trashed, "publish").json()["error"]["code"] == "trashed"
    assert upload(client, "pieces", trashed, jpeg(size=(10, 10)), "hero").status_code in (409, 503)

    restored = post(client, "pieces", trashed, "untrash")
    assert restored.status_code == 200 and restored.json()["trashed_at"] is None
    assert restored.json()["publication_status"] == "draft"
    assert "Máscara de jaguar" in names(client, "pieces")


def test_trash_rules(client):
    artisan = new_artisan(client, name="Beto Cruz")
    piece = new_piece(client, artisan["id"], name="Máscara de diablo")
    # An artisan with pieces outside the trash cannot go there.
    blocked = post(client, "artisans", artisan, "trash")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "has_pieces"
    # A published record must go back to draft first.
    artisan = act(client, "artisans", artisan, "publish").json()
    piece = act(client, "pieces", piece, "publish").json()
    assert post(client, "pieces", piece, "trash").json()["error"]["code"] == "published"
    piece = act(client, "pieces", piece, "unpublish").json()
    piece = post(client, "pieces", piece, "trash").json()
    # It was public once: it can stay in the trash but never be purged.
    assert piece["purge_blocker"] == "may_have_been_public"
    assert post(client, "pieces", piece, "purge").json()["error"]["code"] == "may_have_been_public"
    # A piece cannot come back while its artisan is in the trash.
    artisan = act(client, "artisans", get(client, "artisans", artisan), "unpublish").json()
    artisan = post(client, "artisans", artisan, "trash").json()
    assert post(client, "pieces", piece, "untrash").json()["error"]["code"] == "trashed_artisan"
    assert post(client, "artisans", artisan, "trash").json()["error"]["code"] == "invalid_transition"


def test_purge_deletes_record_media_and_files_and_keeps_the_audit(client, db_session, media_root):
    artisan = new_artisan(client, name="Carla Díaz")
    piece = new_piece(client, artisan["id"], name="Máscara de tigre")
    upload(client, "pieces", piece, jpeg(size=(10, 10)), "hero")
    upload(client, "artisans", artisan, jpeg(size=(10, 10)), "portrait")
    piece = post(client, "pieces", get(client, "pieces", piece), "trash").json()
    assert post(client, "pieces", piece, "purge").status_code == 204
    assert client.get(f"/api/admin/v1/pieces/{piece['id']}", headers=auth(make_token())).status_code == 404

    artisan = get(client, "artisans", artisan)
    artisan = post(client, "artisans", artisan, "trash").json()
    assert artisan["purge_blocker"] is None
    assert post(client, "artisans", artisan, "purge").status_code == 204
    assert names(client, "artisans", trashed="true") == []
    # Only tombstones remain: no published file, no original.
    assert all(p.endswith(".deleted") for p in files_under(media_root))
    actions = set(db_session.execute(select(AuditEvent.action).where(
        AuditEvent.entity_id.in_([uuid.UUID(piece["id"]), uuid.UUID(artisan["id"])]))).scalars())
    assert {"piece.trashed", "piece.deleted", "artisan.trashed", "artisan.deleted"} <= actions


def test_purge_needs_the_trash_the_version_and_no_pieces(client):
    artisan = new_artisan(client, name="Dora Luna")
    piece = new_piece(client, artisan["id"], name="Máscara de toro")
    assert post(client, "pieces", piece, "purge").json()["error"]["code"] == "not_trashed"
    piece = post(client, "pieces", piece, "trash").json()
    stale = client.post(f"/api/admin/v1/pieces/{piece['id']}/purge", json={},
                        headers=H(**{"If-Match": "2020-01-01T00:00:00+00:00"}))
    assert stale.status_code == 412
    artisan = post(client, "artisans", get(client, "artisans", artisan), "trash").json()
    assert artisan["purge_blocker"] == "has_pieces"
    assert post(client, "artisans", artisan, "purge").json()["error"]["code"] == "has_pieces"
