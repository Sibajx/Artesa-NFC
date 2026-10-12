"""Entries of the home's collection block (Gestión → Hero → Colecciones)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.models.audit_event import AuditEvent
from app.services import collections as svc
from tests.test_admin_api import auth, make_token
from tests.test_hero import EDITOR, H, OWNER, hero_client  # noqa: F401 (fixture)
from tests.test_site_images import photo

URL = "/api/admin/v1/hero/collections"
PUBLIC = "/api/v1/collections"


def api(c, method, path="", email=OWNER, **kwargs):
    if method == "delete":  # the write guard wants a JSON body, like the admin UI sends
        return c.request("DELETE", f"{URL}{path}", headers=H(email), json={})
    return getattr(c, method)(f"{URL}{path}", headers=H(email), **kwargs)


def new(c, title="Colección de barro negro", **extra):
    r = api(c, "post", json={"title": title, **extra})
    assert r.status_code == 201, r.text
    return r.json()["items"][-1]


def put_image(c, entry, seed=0, email=OWNER):
    return c.post(f"{URL}/{entry['id']}/image", content=photo(seed=seed), headers={**H(email), "Content-Type": "image/jpeg"})


def folder(entry) -> Path:
    return Path(get_settings().media_root) / "publico" / "sitio" / "colecciones" / entry["id"]


def names(entry) -> set[str]:
    return {p.name for p in folder(entry).iterdir()} if folder(entry).exists() else set()


def test_no_entries_means_the_home_keeps_its_built_in_block(hero_client):
    r = api(hero_client, "get")
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["max_entries"] == svc.MAX_ENTRIES
    assert r.json()["ratio"] == "4:5" and r.json()["media_enabled"] is True
    public = hero_client.get(PUBLIC)
    assert public.json() == {"data": []} and public.headers["cache-control"] == "public, max-age=30"


def test_an_entry_is_not_public_until_it_has_a_photo_and_is_active(hero_client):
    entry = new(hero_client, body="Piezas de San Bartolo Coyotepec.", cta_label="Conocerla", link="/piezas/")
    assert entry["active"] is False and entry["image"] is None
    assert hero_client.get(PUBLIC).json()["data"] == []
    refused = api(hero_client, "patch", f"/{entry['id']}", json={"active": True})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "image_required"
    r = put_image(hero_client, entry)
    assert r.status_code == 200, r.text
    image = r.json()["items"][0]["image"]
    assert (image["width"], image["height"]) == (1200, 1500) and image["jpg"].startswith(f"sitio/colecciones/{entry['id']}/")
    assert hero_client.get(PUBLIC).json()["data"] == []  # a photo alone does not publish it
    assert api(hero_client, "patch", f"/{entry['id']}", json={"active": True}).status_code == 200
    [shown] = hero_client.get(PUBLIC).json()["data"]
    assert shown["id"] == entry["id"] and shown["title"] == "Colección de barro negro" and shown["cta_label"] == "Conocerla"
    assert shown["link"] == "/piezas/" and shown["image"]["jpg"] == f"/media/{image['jpg']}" and shown["body"].startswith("Piezas")
    assert api(hero_client, "patch", f"/{entry['id']}", json={"active": False}).status_code == 200
    assert hero_client.get(PUBLIC).json()["data"] == []


def test_defaults_and_validation(hero_client):
    plain = new(hero_client, title="  Textiles   de Teotitlán ")
    assert plain["title"] == "Textiles de Teotitlán" and plain["cta_label"] is None and plain["link"] is None
    put_image(hero_client, plain)
    api(hero_client, "patch", f"/{plain['id']}", json={"active": True})
    [shown] = hero_client.get(PUBLIC).json()["data"]
    assert shown["cta_label"] == svc.DEFAULT_CTA and shown["link"] == svc.DEFAULT_LINK
    assert api(hero_client, "post", json={"title": "ab"}).json()["error"]["code"] == "invalid_collection"
    for bad in ("javascript:alert(1)", "//evil.example/x", "http://example.com", "data:text/html,x", "/a b", "/\\evil", "https://", "ftp://x"):
        r = api(hero_client, "patch", f"/{plain['id']}", json={"link": bad})
        assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_link", bad
    for good in ("/piezas/", "/artesanos/", "/", "https://artesanfc.com/piezas/?x=1"):
        assert api(hero_client, "patch", f"/{plain['id']}", json={"link": good}).status_code == 200, good
    assert api(hero_client, "patch", f"/{plain['id']}", json={"link": ""}).json()["items"][0]["link"] is None


def test_the_order_is_kept_and_moves_swap_neighbours(hero_client):
    a, b, c = (new(hero_client, title=f"Colección {n}") for n in "ABC")
    ids = lambda: [x["id"] for x in api(hero_client, "get").json()["items"]]  # noqa: E731
    assert ids() == [a["id"], b["id"], c["id"]]
    api(hero_client, "post", f"/{c['id']}/move", json={"direction": "up"})
    assert ids() == [a["id"], c["id"], b["id"]]
    api(hero_client, "post", f"/{a['id']}/move", json={"direction": "up"})  # already first: nothing changes
    assert ids() == [a["id"], c["id"], b["id"]]
    api(hero_client, "post", f"/{a['id']}/move", json={"direction": "down"})
    assert ids() == [c["id"], a["id"], b["id"]]
    for e in (a, b, c):
        put_image(hero_client, e)
        api(hero_client, "patch", f"/{e['id']}", json={"active": True})
    assert [x["id"] for x in hero_client.get(PUBLIC).json()["data"]] == [c["id"], a["id"], b["id"]]
    assert api(hero_client, "post", f"/{a['id']}/move", json={"direction": "sideways"}).status_code == 422


def test_replacing_the_photo_removes_the_old_files_and_deleting_removes_everything(hero_client):
    entry = new(hero_client)
    put_image(hero_client, entry, seed=1)
    first = names(entry)
    assert len(first) == 3
    put_image(hero_client, entry, seed=2)
    second = names(entry)
    assert len(second) == 3 and not (first & second)
    other = new(hero_client, title="Otra colección")
    put_image(hero_client, other)
    assert api(hero_client, "delete", f"/{entry['id']}").status_code == 200
    assert not folder(entry).exists() and len(names(other)) == 3
    assert [x["id"] for x in api(hero_client, "get").json()["items"]] == [other["id"]]
    assert api(hero_client, "delete", f"/{entry['id']}").status_code == 404


def test_at_most_six_entries(hero_client):
    for n in range(svc.MAX_ENTRIES):
        new(hero_client, title=f"Colección {n}")
    over = api(hero_client, "post", json={"title": "Una más"})
    assert over.status_code == 409 and over.json()["error"]["code"] == "too_many_collections"


def test_only_hero_accounts_and_the_owner_manage_collections_but_everyone_reads_the_public_list(hero_client):
    assert hero_client.get(URL, headers=auth(make_token(email=EDITOR))).status_code == 403
    assert api(hero_client, "post", email=EDITOR, json={"title": "Colección de prueba"}).status_code == 403
    assert hero_client.post(URL, json={"title": "Colección de prueba"}, headers=auth(make_token(email=OWNER))).status_code == 403  # no CSRF header
    assert hero_client.get(PUBLIC).status_code == 200


def test_bad_photos_are_refused_and_changes_are_audited(hero_client, db_session):
    entry = new(hero_client)
    bad = hero_client.post(f"{URL}/{entry['id']}/image", content=b"not an image", headers={**H(), "Content-Type": "image/jpeg"})
    assert bad.status_code == 422
    tiny = hero_client.post(f"{URL}/{entry['id']}/image", content=photo(size=(300, 400)), headers={**H(), "Content-Type": "image/jpeg"})
    assert tiny.status_code == 422 and tiny.json()["error"]["code"] == "too_small"
    wrong = hero_client.post(f"{URL}/{entry['id']}/image", content=b"x", headers={**H(), "Content-Type": "text/plain"})
    assert wrong.status_code == 415
    assert hero_client.post(f"{URL}/00000000-0000-0000-0000-000000000000/image", content=photo(),
                            headers={**H(), "Content-Type": "image/jpeg"}).status_code == 404
    put_image(hero_client, entry)
    api(hero_client, "patch", f"/{entry['id']}", json={"active": True})
    api(hero_client, "delete", f"/{entry['id']}")
    actions = [a for (a,) in db_session.execute(select(AuditEvent.action).where(AuditEvent.entity_type == "collection_entry")
                                                .order_by(AuditEvent.occurred_at))]
    assert actions == ["collection_entry.created", "collection_entry.image_set", "collection_entry.updated", "collection_entry.deleted"]
