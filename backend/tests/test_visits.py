"""Visit log: visits to artisans and galleries (Visits permission only)."""
from __future__ import annotations

import io
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select, update

from app.core.config import get_settings
from app.models.admin_account import AdminAccount
from app.models.audit_event import AuditEvent
from tests.test_accounts_and_authorization import FIXED, NEW, OWNER, H, owner_client  # noqa: F401
from tests.test_admin_api import auth, make_token

API = "/api/admin/v1"
GPS_TAG = 0x8825


@pytest.fixture()
def media_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "media"
    (root / "originales").mkdir(parents=True)
    (root / "publico").mkdir()
    monkeypatch.setattr(get_settings(), "media_root", str(root))
    return root


def jpeg(size=(2400, 1800)) -> bytes:
    image = Image.new("RGB", size, (40, 90, 160))
    exif = Image.Exif()
    exif[GPS_TAG] = {1: "N", 2: (17.0, 3.0, 36.0), 3: "W", 4: (96.0, 43.0, 12.0)}
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()


def post(client, path, body=None, email=OWNER):
    return client.post(f"{API}{path}", json=body if body is not None else {}, headers=H(email))


def get(client, path, email=OWNER):
    return client.get(f"{API}{path}", headers=auth(make_token(email=email)))


def visit_body(**extra):
    return {"visited_on": date.today().isoformat(), "kind": "artesano",
            "summary": "Platicamos de los materiales y de la próxima entrega.", **extra}


def new_visit(client, **extra):
    r = post(client, "/visits", visit_body(**extra))
    assert r.status_code == 201, r.text
    return r.json()


def upload(client, visit, data, email=OWNER, content_type="image/jpeg"):
    return client.post(f"{API}/visits/{visit['id']}/photo", content=data, headers=H(email, **{"Content-Type": content_type}))


def files(root: Path):
    return [p for p in root.rglob("*") if p.is_file()]


def give_visits(client, db_session, email=NEW, extra=("view",)):
    post(client, "/accounts", {"email": email, "role": "editor"})
    db_session.execute(update(AdminAccount).where(AdminAccount.email == email).values(permissions=[*extra, "visits"]))
    db_session.commit()


def test_only_people_with_the_visits_permission_can_read_or_write(owner_client, db_session):
    new_visit(owner_client)
    # An editor does not have it, not even to read.
    post(owner_client, "/accounts", {"email": NEW, "role": "editor"})
    assert get(owner_client, "/visits", NEW).status_code == 403
    assert post(owner_client, "/visits", visit_body(), NEW).status_code == 403
    # Given by hand (the matrix), it opens everything but deleting.
    db_session.execute(update(AdminAccount).where(AdminAccount.email == NEW).values(permissions=["view", "visits"]))
    db_session.commit()
    assert get(owner_client, "/visits", NEW).status_code == 200
    mine = post(owner_client, "/visits", visit_body(), NEW).json()
    assert mine["recorded_by"] == NEW
    assert post(owner_client, f"/visits/{mine['id']}/delete", {}, NEW).status_code == 403
    # Fixed editors without it stay out; the owner sees all.
    assert get(owner_client, "/visits", FIXED).status_code == 403
    assert len(get(owner_client, "/visits").json()["data"]) == 2


def test_the_summary_is_mandatory_and_the_rest_is_checked(owner_client):
    assert post(owner_client, "/visits", visit_body(summary="   corto  ")).json()["error"]["code"] == "summary_required"
    assert post(owner_client, "/visits", {"visited_on": date.today().isoformat(), "kind": "otro"}).status_code == 422
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert post(owner_client, "/visits", visit_body(visited_on=tomorrow)).json()["error"]["code"] == "invalid_visit"
    assert post(owner_client, "/visits", visit_body(kind="galeria")).json()["error"]["code"] == "place_required"
    assert post(owner_client, "/visits", visit_body(kind="visita")).status_code == 422
    assert post(owner_client, "/visits", visit_body(artisan_id=str(uuid.uuid4()))).status_code == 404
    ok = new_visit(owner_client, kind="galeria", place="Galería Oaxaca Centro", agreements="Llevar 3 piezas el 20 de octubre.")
    assert ok["consent_to_publish"] is False and ok["photo"] is None and ok["place"] == "Galería Oaxaca Centro"


def test_listing_filter_and_edit(owner_client):
    artisan = post(owner_client, "/artisans", {"full_name": "Rigoberto Ramírez Robles"}).json()
    a = new_visit(owner_client, artisan_id=artisan["id"], visited_on=(date.today() - timedelta(days=3)).isoformat())
    b = new_visit(owner_client, kind="galeria", place="Galería X")
    ids = [v["id"] for v in get(owner_client, "/visits").json()["data"]]
    assert ids.index(b["id"]) < ids.index(a["id"])  # newest visit first
    only = get(owner_client, f"/visits?artisan_id={artisan['id']}").json()["data"]
    assert [v["id"] for v in only] == [a["id"]] and only[0]["artisan_name"] == "Rigoberto Ramírez Robles"
    r = owner_client.patch(f"{API}/visits/{a['id']}", json={"agreements": "Pedir más barro.", "consent_to_publish": True}, headers=H())
    assert r.status_code == 200 and r.json()["agreements"] == "Pedir más barro." and r.json()["consent_to_publish"] is True
    assert owner_client.patch(f"{API}/visits/{a['id']}", json={"summary": "x"}, headers=H()).json()["error"]["code"] == "summary_required"
    assert owner_client.patch(f"{API}/visits/{a['id']}", json={"kind": "galeria"}, headers=H()).json()["error"]["code"] == "place_required"
    assert get(owner_client, f"/visits/{uuid.uuid4()}").status_code == 404


def test_one_private_photo_per_visit(owner_client, media_root, db_session):
    v = new_visit(owner_client)
    assert upload(owner_client, v, jpeg()).status_code == 201
    [stored] = files(media_root)
    assert stored.relative_to(media_root).parts[:2] == ("privado", "visitas") and oct(stored.stat().st_mode)[-3:] == "600"
    with Image.open(stored) as img:
        assert max(img.size) <= 1600 and GPS_TAG not in img.getexif()
    url = get(owner_client, f"/visits/{v['id']}").json()["photo"]
    got = owner_client.get(url, headers=auth(make_token(email=OWNER)))
    assert got.status_code == 200 and "no-store" in got.headers["cache-control"]
    assert owner_client.get(url).status_code == 401
    # A second upload replaces the first (still one file).
    assert upload(owner_client, v, jpeg(size=(80, 60))).status_code == 201
    assert len(files(media_root)) == 1 and files(media_root)[0] != stored
    assert upload(owner_client, v, b"not an image").status_code == 422
    assert upload(owner_client, v, b"x", content_type="text/plain").status_code == 415
    # Without the permission the photo is not served either.
    post(owner_client, "/accounts", {"email": NEW, "role": "editor"})
    assert owner_client.get(url, headers=auth(make_token(email=NEW))).status_code == 403
    # Removing it deletes the file.
    assert post(owner_client, f"/visits/{v['id']}/photo/delete").json()["photo"] is None
    assert files(media_root) == []
    assert post(owner_client, f"/visits/{v['id']}/photo/delete").json()["error"]["code"] == "no_photo"


def test_only_the_owner_deletes_a_visit_and_its_photo(owner_client, media_root, db_session):
    v = new_visit(owner_client)
    upload(owner_client, v, jpeg(size=(60, 40)))
    assert len(files(media_root)) == 1
    assert post(owner_client, f"/visits/{v['id']}/delete").status_code == 204
    assert files(media_root) == [] and get(owner_client, f"/visits/{v['id']}").status_code == 404
    actions = [a for (a,) in db_session.execute(select(AuditEvent.action).where(AuditEvent.entity_type == "visit")
                                                .order_by(AuditEvent.occurred_at))]
    assert actions == ["visit.created", "visit.photo_set", "visit.deleted"]


def test_without_a_media_folder_the_text_still_works(owner_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "media_root", "")
    v = new_visit(owner_client)
    assert upload(owner_client, v, jpeg()).status_code >= 400
    assert get(owner_client, f"/visits/{v['id']}").json()["photo"] is None
