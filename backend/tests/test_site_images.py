"""P-029: the replaceable images of the public site (Gestión → Hero)."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models.audit_event import AuditEvent
from app.models.site_image import SiteImage
from app.services import site_images as svc
from tests.test_admin_api import auth, make_token
from tests.test_hero import EDITOR, H, OWNER, hero_client  # noqa: F401 (fixture)

URL = "/api/admin/v1/hero/site-images"
SLOT = "collection-entry"


def photo(size=(3000, 2000), fmt="JPEG", mode="RGB", seed=0) -> bytes:
    image = Image.new(mode, size)
    px = image.load()
    for x in range(0, size[0], 7):  # a cheap pattern so different seeds give different bytes
        for y in range(0, size[1], 7):
            px[x, y] = ((x + seed * 40) % 256, (y * 2) % 256, (x + y) % 256) + ((255,) if mode == "RGBA" else ())
    out = io.BytesIO()
    image.save(out, fmt)
    return out.getvalue()


def put(c, data, content_type="image/jpeg", email=OWNER, slot=SLOT):
    return c.post(f"{URL}/{slot}", content=data, headers={**H(email), "Content-Type": content_type})


def public_root() -> Path:
    return Path(get_settings().media_root) / "publico"


def files() -> set[str]:
    folder = public_root() / "sitio" / SLOT
    return {p.name for p in folder.iterdir()} if folder.exists() else set()


def test_a_slot_without_a_photo_keeps_the_provisional_image(hero_client):
    r = hero_client.get(URL, headers=H())
    assert r.status_code == 200 and r.json()["media_enabled"] is True
    assert r.json()["slots"] == [{"slot": SLOT, "label": "Entrada a la colección", "ratio": "4:5", "image": None}]
    public = hero_client.get("/api/v1/site-images")
    assert public.json() == {"data": {}} and public.headers["cache-control"] == "public, max-age=300"


def test_a_photo_is_cropped_converted_and_served(hero_client, db_session):
    r = put(hero_client, photo())
    assert r.status_code == 200, r.text
    image = r.json()["slots"][0]["image"]
    assert (image["width"], image["height"]) == (1200, 1500)
    assert image["avif"].startswith(f"sitio/{SLOT}/") and image["jpg"].endswith(".jpg")

    jpg = Image.open(public_root() / image["jpg"])
    assert jpg.size == (1200, 1500) and "exif" not in jpg.info
    assert Image.open(public_root() / image["webp"]).size == (1200, 1500)
    assert (public_root() / image["avif"]).stat().st_size > 0
    for key, mime in (("avif", "image/avif"), ("webp", "image/webp"), ("jpg", "image/jpeg")):
        served = hero_client.get(f"/media/{image[key]}")
        assert served.status_code == 200 and served.headers["content-type"] == mime

    body = hero_client.get("/api/v1/site-images").json()["data"][SLOT]
    assert body == {"avif": f"/media/{image['avif']}", "webp": f"/media/{image['webp']}",
                    "jpg": f"/media/{image['jpg']}", "width": 1200, "height": 1500}
    actions = [e.action for e in db_session.execute(select(AuditEvent).where(AuditEvent.entity_type == "site_image")).scalars()]
    assert actions == ["site_image.updated"]


def test_a_small_png_with_transparency_works_at_its_own_size(hero_client):
    r = put(hero_client, photo(size=(800, 1000), fmt="PNG", mode="RGBA"), "image/png")
    assert r.status_code == 200, r.text
    assert (r.json()["slots"][0]["image"]["width"], r.json()["slots"][0]["image"]["height"]) == (800, 1000)


def test_replacing_a_photo_removes_the_old_files_and_the_same_photo_keeps_them(hero_client):
    data = photo()
    put(hero_client, data)
    first = files()
    assert len(first) == 3
    put(hero_client, data)  # the same photo: same names, nothing lost
    assert files() == first
    put(hero_client, photo(seed=3))
    second = files()
    assert len(second) == 3 and not (first & second)
    assert hero_client.get("/api/v1/site-images").json()["data"][SLOT]["jpg"].split("/")[-1] in second


def test_clearing_goes_back_to_the_provisional_image(hero_client, db_session):
    put(hero_client, photo())
    r = hero_client.delete(f"{URL}/{SLOT}", headers={**H(), "Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["slots"][0]["image"] is None
    assert files() == set() and db_session.get(SiteImage, SLOT) is None
    assert hero_client.get("/api/v1/site-images").json() == {"data": {}}
    # Clearing an empty slot is fine.
    assert hero_client.delete(f"{URL}/{SLOT}", headers={**H(), "Content-Type": "application/json"}).status_code == 200


def test_bad_uploads_are_refused_and_leave_nothing(hero_client, monkeypatch):
    r = put(hero_client, b"GIF89a" + b"\x00" * 50)
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_type"
    r = put(hero_client, photo(size=(500, 625)))
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_small"
    assert put(hero_client, b"").status_code == 422
    assert put(hero_client, b"<html></html>", "text/html").status_code == 415
    monkeypatch.setattr(svc, "MAX_BYTES", 100)
    r = put(hero_client, b"\xff\xd8" + b"\x00" * 400)
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    assert files() == set()


def test_only_hero_accounts_may_touch_the_site_images(hero_client):
    assert hero_client.get(URL, headers=H(EDITOR)).status_code == 403
    assert put(hero_client, photo(size=(900, 1125)), email=EDITOR).status_code == 403
    assert hero_client.delete(f"{URL}/{SLOT}", headers={**H(EDITOR), "Content-Type": "application/json"}).status_code == 403
    no_csrf = hero_client.post(f"{URL}/{SLOT}", content=photo(size=(900, 1125)), headers={
        "Content-Type": "image/jpeg", **auth(make_token(email=OWNER))})
    assert no_csrf.status_code == 403
    assert put(hero_client, photo(size=(900, 1125)), slot="nope").status_code == 404


def test_two_people_uploading_the_first_photo_at_once_do_not_break_it(hero_client, db_session, monkeypatch):
    real, state = db_session.commit, {"raised": False}

    def flaky():
        if not state["raised"] and any(isinstance(o, SiteImage) for o in db_session.new):
            state["raised"] = True
            raise IntegrityError("INSERT INTO site_image", {}, Exception("duplicate key value"))
        return real()

    monkeypatch.setattr(db_session, "commit", flaky)
    r = put(hero_client, photo(size=(900, 1125)))
    assert r.status_code == 200, r.text
    assert state["raised"] and db_session.get(SiteImage, SLOT) is not None
    assert len(files()) == 3
