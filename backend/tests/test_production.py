"""Production follow-up of a piece: steps, the board, and private photos."""
from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select, update

from app.core.config import get_settings
from app.models.admin_account import AdminAccount
from app.models.audit_event import AuditEvent
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.models.piece_location import PieceLocation
from app.models.production import ProductionPhoto
from app.services import private_photos
from tests.test_accounts_and_authorization import FIXED, NEW, OWNER, H, owner_client  # noqa: F401
from tests.test_admin_api import auth, make_token

API = "/api/admin/v1/production"
GPS_TAG = 0x8825


@pytest.fixture()
def media_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "media"
    (root / "originales").mkdir(parents=True)
    (root / "publico").mkdir()
    monkeypatch.setattr(get_settings(), "media_root", str(root))
    return root


def jpeg(size=(2400, 1800)) -> bytes:
    image = Image.new("RGB", size, (180, 40, 40))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    exif[GPS_TAG] = {1: "N", 2: (17.0, 3.0, 36.0), 3: "W", 4: (96.0, 43.0, 12.0)}
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()


def post(client, path, body=None, email=OWNER):
    return client.post(f"/api/admin/v1{path}", json=body if body is not None else {}, headers=H(email))


def get(client, path, email=OWNER):
    return client.get(f"{API}{path}", headers=auth(make_token(email=email)))


def upload(client, piece, step, data, email=OWNER, content_type="image/jpeg"):
    return client.post(f"{API}/pieces/{piece['id']}/steps/{step}/photos", content=data,
                       headers=H(email, **{"Content-Type": content_type}))


@pytest.fixture()
def piece(owner_client):
    artisan = post(owner_client, "/artisans", {"full_name": "María López Ruiz"}).json()
    return post(owner_client, "/pieces", {"artisan_id": artisan["id"], "name": "Máscara de tigre"}).json()


def mark(client, piece, step, email=OWNER, **body):
    return client.post(f"{API}/pieces/{piece['id']}/steps/{step}", json=body, headers=H(email))


def steps(client, piece):
    return {s["step"]: s for s in get(client, f"/pieces/{piece['id']}").json()["steps"]}


def test_a_new_piece_has_six_steps_none_done(owner_client, piece):
    body = get(owner_client, f"/pieces/{piece['id']}").json()
    assert [s["step"] for s in body["steps"]] == ["received", "chip_placed", "chip_programmed", "packed", "shipped", "delivered"]
    assert not any(s["done"] for s in body["steps"]) and body["name"] == "Máscara de tigre"


def test_marking_steps_moves_the_piece_along_the_board(owner_client, piece):
    def card():
        board = get(owner_client, "/board").json()
        return next(c for c in board["data"] if c["piece_id"] == piece["id"]), board["counts"]

    assert card()[0]["bucket"] == "pending"
    assert mark(owner_client, piece, "received", note="Llegó en buen estado").status_code == 201
    assert card()[0]["bucket"] == "in_process"
    mark(owner_client, piece, "chip_placed")
    mark(owner_client, piece, "packed")
    assert card()[0]["bucket"] == "ready_to_ship"
    r = mark(owner_client, piece, "shipped", carrier="Estafeta", tracking="1234567890")
    assert r.status_code == 201
    c, counts = card()
    assert c["bucket"] == "in_transit" and c["done"] == 4 and c["last_step"] == "shipped" and counts["in_transit"] >= 1
    s = steps(owner_client, piece)["shipped"]
    assert s["carrier"] == "Estafeta" and s["tracking"] == "1234567890" and s["done_by"] == OWNER and s["source"] == "manual"
    mark(owner_client, piece, "delivered")
    assert card()[0]["bucket"] == "delivered"


def test_the_chip_step_and_a_recorded_delivery_come_from_existing_data(owner_client, piece, db_session):
    assert mark(owner_client, piece, "chip_programmed").json()["error"]["code"] == "automatic_step"
    db_session.add(NfcTag(piece_id=uuid.UUID(piece["id"]), chip_model="NTAG213", status=NfcTagStatus.programmed))
    db_session.add(PieceLocation(piece_id=uuid.UUID(piece["id"]), location="entregada", moved_on=__import__("datetime").date.today(),
                                 recorded_by=OWNER))
    db_session.commit()
    s = steps(owner_client, piece)
    assert s["chip_programmed"]["done"] and s["chip_programmed"]["source"] == "auto"
    assert s["delivered"]["done"] and s["delivered"]["source"] == "auto"


def test_rules_of_the_steps(owner_client, piece):
    assert mark(owner_client, piece, "received").status_code == 201
    assert mark(owner_client, piece, "received").json()["error"]["code"] == "already_done"
    assert mark(owner_client, piece, "packed", carrier="DHL").json()["error"]["code"] == "invalid_step"
    assert mark(owner_client, piece, "teleported").status_code == 404
    assert owner_client.post(f"{API}/pieces/{uuid.uuid4()}/steps/received", json={}, headers=H()).status_code == 404
    # Undo, then it can be marked again.
    assert owner_client.post(f"{API}/pieces/{piece['id']}/steps/received/undo", json={}, headers=H()).status_code == 200
    assert not steps(owner_client, piece)["received"]["done"]
    assert owner_client.post(f"{API}/pieces/{piece['id']}/steps/received/undo", json={}, headers=H()).json()["error"]["code"] == "step_not_done"
    assert mark(owner_client, piece, "received").status_code == 201


def test_photos_are_private_clean_and_limited(owner_client, piece, media_root, db_session):
    mark(owner_client, piece, "chip_placed")
    r = upload(owner_client, piece, "chip_placed", jpeg())
    assert r.status_code == 201, r.text
    url = steps(owner_client, piece)["chip_placed"]["photos"][0]
    # Stored under privado/, never under publico/, re-encoded without GPS/EXIF and shrunk.
    files = [p for p in media_root.rglob("*") if p.is_file()]
    assert len(files) == 1 and files[0].relative_to(media_root).parts[0] == "privado"
    assert oct(files[0].stat().st_mode)[-3:] == "600"
    with Image.open(files[0]) as stored:
        assert max(stored.size) <= 1600 and GPS_TAG not in stored.getexif() and 0x010F not in stored.getexif()
    # Served to signed-in admins only, never cached.
    got = owner_client.get(url, headers=auth(make_token(email=OWNER)))
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg" and "no-store" in got.headers["cache-control"]
    assert owner_client.get(url).status_code == 401
    # A step must be marked first; five photos at most; only images.
    assert upload(owner_client, piece, "packed", jpeg()).json()["error"]["code"] == "step_not_done"
    assert upload(owner_client, piece, "chip_placed", b"not an image").status_code == 422
    assert upload(owner_client, piece, "chip_placed", b"x", content_type="text/plain").status_code == 415
    for _ in range(4):
        assert upload(owner_client, piece, "chip_placed", jpeg(size=(40, 30))).status_code == 201
    assert upload(owner_client, piece, "chip_placed", jpeg()).json()["error"]["code"] == "too_many_photos"
    # Deleting a photo and undoing the step remove the files.
    photo = db_session.execute(select(ProductionPhoto)).scalars().first()
    assert owner_client.post(f"{API}/photos/{photo.id}/delete", json={}, headers=H()).status_code == 200
    assert len([p for p in media_root.rglob("*") if p.is_file()]) == 4
    assert owner_client.post(f"{API}/pieces/{piece['id']}/steps/chip_placed/undo", json={}, headers=H()).status_code == 200
    assert [p for p in media_root.rglob("*") if p.is_file()] == []


def test_without_a_media_folder_steps_work_and_photos_say_so(owner_client, piece, monkeypatch):
    monkeypatch.setattr(get_settings(), "media_root", "")
    mark(owner_client, piece, "received")
    assert get(owner_client, f"/pieces/{piece['id']}").json()["photos_enabled"] is False
    assert upload(owner_client, piece, "received", jpeg()).status_code >= 400


def test_private_paths_cannot_escape(media_root):
    outside = media_root / "originales" / "secret.jpg"
    outside.write_bytes(b"x")
    (media_root / "privado").mkdir()
    assert private_photos.resolve(media_root, "originales/secret.jpg") is None
    assert private_photos.resolve(media_root, "privado/../originales/secret.jpg") is None
    assert private_photos.resolve(media_root, "privado/none.jpg") is None


def test_who_can_read_and_who_can_mark(owner_client, piece, db_session):
    post(owner_client, "/accounts", {"email": NEW, "role": "editor"})
    assert mark(owner_client, piece, "received", email=NEW).status_code == 201  # editors hold Logistics
    db_session.execute(update(AdminAccount).where(AdminAccount.email == NEW).values(permissions=["view", "sales"]))
    db_session.commit()
    assert get(owner_client, "/board", NEW).status_code == 200
    assert get(owner_client, f"/pieces/{piece['id']}", NEW).status_code == 200
    assert mark(owner_client, piece, "packed", email=NEW).status_code == 403
    assert owner_client.post(f"{API}/pieces/{piece['id']}/steps/received/undo", json={}, headers=H(NEW)).status_code == 403
    assert upload(owner_client, piece, "received", jpeg(), email=NEW).status_code == 403


def test_steps_are_audited(owner_client, piece, db_session):
    mark(owner_client, piece, "received")
    mark(owner_client, piece, "shipped", carrier="DHL", tracking="X1")
    owner_client.post(f"{API}/pieces/{piece['id']}/steps/shipped/undo", json={}, headers=H())
    actions = [a for (a,) in db_session.execute(select(AuditEvent.action).where(
        AuditEvent.entity_id == uuid.UUID(piece["id"]), AuditEvent.action.like("piece.production%")).order_by(AuditEvent.occurred_at))]
    assert actions == ["piece.production_step", "piece.production_step", "piece.production_step_undone"]
