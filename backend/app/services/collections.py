"""Entries of the home's collection block (Gestión → Hero → Colecciones).

A photo (cropped to 4:5, converted and named by content like every site image),
a title, a short text and a button that goes to a page of the site. The home
shows the *active* entries that have a photo, in order, and rotates between
them when there is more than one. An entry cannot be active without a photo.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.collection_entry import CollectionEntry
from app.services import site_images
from app.services.content import Actor, ContentConflict, ContentNotFound, _audit

MAX_ENTRIES = 6
DEFAULT_CTA = "Ver la colección"
DEFAULT_LINK = "/piezas/"
RATIO = (4, 5)
MAX_WIDTH = 1200
# A path of this site, or an https address. Nothing else (no javascript:, data: or protocol-relative links).
_LINK_OK = re.compile(r"^(?:/(?!/)[^\s\\]*|https://[^\s\\/][^\s\\]*)$")


def _slot(entry_id: uuid.UUID) -> site_images.Slot:
    return site_images.Slot(f"colecciones/{entry_id}", "Colección", RATIO, MAX_WIDTH)


def get(db: Session, entry_id: uuid.UUID) -> CollectionEntry:
    entry = db.get(CollectionEntry, entry_id)
    if entry is None:
        raise ContentNotFound("not_found", "Esa colección no existe.")
    return entry


def listing(db: Session) -> list[CollectionEntry]:
    return list(db.execute(select(CollectionEntry).order_by(CollectionEntry.position, CollectionEntry.created_at)).scalars())


def public_entries(db: Session) -> list[CollectionEntry]:
    return [e for e in listing(db) if e.active and e.jpg]


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "title" in data:
        title = " ".join((data["title"] or "").split())
        if len(title) < 3:
            raise ContentConflict("invalid_collection", "Escribe el título (al menos 3 letras).", "title")
        out["title"] = title[:80]
    if "body" in data:
        out["body"] = (data["body"] or "").strip()[:240] or None
    if "cta_label" in data:
        out["cta_label"] = " ".join((data["cta_label"] or "").split())[:40] or None
    if "link" in data:
        link = (data["link"] or "").strip()
        if link and (len(link) > 300 or not _LINK_OK.fullmatch(link)):
            raise ContentConflict("invalid_link", "El enlace debe ser una página de este sitio (empieza con /) "
                                                  "o una dirección que empiece con https://.", "link")
        out["link"] = link or None
    return out


def create(db: Session, actor: Actor, data: dict[str, Any]) -> CollectionEntry:
    clean = _clean({"title": data.get("title"), **{k: data[k] for k in ("body", "cta_label", "link") if k in data}})
    rows = listing(db)
    if len(rows) >= MAX_ENTRIES:
        raise ContentConflict("too_many_collections", f"Hay un máximo de {MAX_ENTRIES} colecciones. Quita una para crear otra.")
    entry = CollectionEntry(position=(rows[-1].position + 1) if rows else 0, active=False,
                            updated_by=actor.identity.email, **clean)
    db.add(entry)
    db.flush()
    _audit(db, actor, "collection_entry", entry.id, "collection_entry.created", {"title": entry.title})
    db.commit()
    return entry


def update(db: Session, actor: Actor, entry_id: uuid.UUID, changes: dict[str, Any]) -> CollectionEntry:
    entry = get(db, entry_id)
    clean = _clean(changes)
    if "active" in changes and changes["active"] is not None:
        if changes["active"] and not entry.jpg:
            raise ContentConflict("image_required", "Sube la foto antes de mostrar esta colección en el sitio.", "active")
        clean["active"] = bool(changes["active"])
    for key, value in clean.items():
        setattr(entry, key, value)
    entry.updated_by = actor.identity.email
    _audit(db, actor, "collection_entry", entry.id, "collection_entry.updated", {"fields": sorted(clean)})
    db.commit()
    return entry


def set_image(db: Session, actor: Actor, public_root: Path, entry_id: uuid.UUID, data: bytes) -> CollectionEntry:
    entry = get(db, entry_id)
    slot = _slot(entry.id)
    rendered = site_images.render(data, slot)
    paths = site_images._store(public_root, slot, rendered)
    old = [p for p in (entry.avif, entry.webp, entry.jpg) if p]
    entry.avif, entry.webp, entry.jpg = paths["avif"], paths["webp"], paths["jpg"]
    entry.width, entry.height = rendered.width, rendered.height
    entry.updated_by = actor.identity.email
    _audit(db, actor, "collection_entry", entry.id, "collection_entry.image_set",
           {"bytes": len(data), "width": rendered.width, "height": rendered.height})
    db.commit()
    site_images._remove(public_root, [p for p in old if p not in paths.values()])
    return entry


def move(db: Session, actor: Actor, entry_id: uuid.UUID, direction: str) -> None:
    rows = listing(db)
    index = next((i for i, e in enumerate(rows) if e.id == entry_id), None)
    if index is None:
        raise ContentNotFound("not_found", "Esa colección no existe.")
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(rows):
        rows[index], rows[target] = rows[target], rows[index]
    for position, row in enumerate(rows):  # renumber: no ties, no gaps
        row.position = position
    _audit(db, actor, "collection_entry", entry_id, "collection_entry.moved", {"direction": direction})
    db.commit()


def delete(db: Session, actor: Actor, public_root: Path | None, entry_id: uuid.UUID) -> None:
    entry = get(db, entry_id)
    old = [p for p in (entry.avif, entry.webp, entry.jpg) if p]
    _audit(db, actor, "collection_entry", entry.id, "collection_entry.deleted", {"title": entry.title})
    db.delete(entry)
    db.flush()
    for position, row in enumerate(listing(db)):
        row.position = position
    db.commit()
    if public_root is not None:
        site_images._remove(public_root, old)
        folder = public_root / "sitio" / "colecciones" / str(entry_id)
        try:
            folder.rmdir()
        except OSError:
            pass


def public_view(entry: CollectionEntry) -> dict[str, Any]:
    return {"id": str(entry.id), "title": entry.title, "body": entry.body,
            "cta_label": entry.cta_label or DEFAULT_CTA, "link": entry.link or DEFAULT_LINK,
            "image": {"avif": f"/media/{entry.avif}", "webp": f"/media/{entry.webp}", "jpg": f"/media/{entry.jpg}",
                      "width": entry.width, "height": entry.height}}
