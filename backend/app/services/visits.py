"""Visit log: visits to artisans and galleries. The written summary is
mandatory (what was said); photos are optional (up to ``MAX_PHOTOS``), stored privately."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.artisan import Artisan
from app.models.visit import VISIT_KINDS, Visit
from app.models.visit_photo import MAX_PHOTOS, VisitPhoto
from app.services import private_photos
from app.services.content import Actor, ContentConflict, ContentNotFound, _audit

MIN_SUMMARY = 10


def _clean(value: str | None, limit: int) -> str | None:
    value = (value or "").strip()
    return value[:limit] or None


def get(db: Session, visit_id: uuid.UUID) -> Visit:
    visit = db.get(Visit, visit_id)
    if visit is None:
        raise ContentNotFound("not_found", "Esa visita no existe.")
    return visit


def listing(db: Session, artisan_id: uuid.UUID | None = None, limit: int = 200) -> list[tuple[Visit, str | None]]:
    query = (select(Visit, Artisan.full_name).outerjoin(Artisan, Artisan.id == Visit.artisan_id)
             .order_by(Visit.visited_on.desc(), Visit.created_at.desc()).limit(limit))
    if artisan_id is not None:
        query = query.where(Visit.artisan_id == artisan_id)
    return list(db.execute(query).all())


def artisan_name(db: Session, visit: Visit) -> str | None:
    """Who was visited: the registered artisan's name, or the one typed by hand."""
    if visit.artisan_id:
        return db.execute(select(Artisan.full_name).where(Artisan.id == visit.artisan_id)).scalar_one_or_none()
    return visit.unregistered_artisan


def _validated(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "visited_on" in data:
        if data["visited_on"] > date.today():
            raise ContentConflict("invalid_visit", "La fecha de la visita no puede ser futura.", "visited_on")
        out["visited_on"] = data["visited_on"]
    if "kind" in data:
        if data["kind"] not in VISIT_KINDS:
            raise ContentConflict("invalid_visit", "Tipo de visita desconocido.", "kind")
        out["kind"] = data["kind"]
    if "summary" in data:
        summary = (data["summary"] or "").strip()
        if len(summary) < MIN_SUMMARY:
            raise ContentConflict("summary_required", "Escribe el resumen de lo que pasó (al menos 10 letras).", "summary")
        out["summary"] = summary[:5000]
    if "artisan_id" in data:
        if data["artisan_id"] is not None and db.get(Artisan, data["artisan_id"]) is None:
            raise ContentNotFound("not_found", "Ese artesano no existe.")
        out["artisan_id"] = data["artisan_id"]
    for key, limit in (("place", 200), ("attendees", 300), ("agreements", 3000), ("unregistered_artisan", 200)):
        if key in data:
            out[key] = _clean(data[key], limit)
    name = out.get("unregistered_artisan")
    if name is not None and len(name) < 2:
        raise ContentConflict("invalid_visit", "Escribe el nombre completo del artesano.", "unregistered_artisan")
    # A visit has a registered artisan or a typed name, never both: choosing a registered
    # one clears the typed name, and typing a name clears the registered one.
    if out.get("artisan_id") is not None:
        out["unregistered_artisan"] = None
    elif name is not None:
        out["artisan_id"] = None
    if "consent_to_publish" in data:
        out["consent_to_publish"] = bool(data["consent_to_publish"])
    return out


def create(db: Session, actor: Actor, data: dict[str, Any]) -> Visit:
    clean = _validated(db, data)
    if clean.get("kind") == "galeria" and not clean.get("place"):
        raise ContentConflict("place_required", "Escribe el nombre de la galería.", "place")
    visit = Visit(recorded_by=actor.identity.email, **clean)
    db.add(visit)
    db.flush()
    _audit(db, actor, "visit", visit.id, "visit.created", {"kind": visit.kind, "visited_on": visit.visited_on.isoformat(),
                                                          "artisan_id": str(visit.artisan_id) if visit.artisan_id else None,
                                                          "unregistered_artisan": visit.unregistered_artisan is not None})
    db.commit()
    return visit


def update(db: Session, actor: Actor, visit_id: uuid.UUID, changes: dict[str, Any]) -> Visit:
    visit = get(db, visit_id)
    clean = _validated(db, changes)
    for key, value in clean.items():
        setattr(visit, key, value)
    if (clean.get("kind") or visit.kind) == "galeria" and not visit.place:
        db.rollback()
        raise ContentConflict("place_required", "Escribe el nombre de la galería.", "place")
    visit.updated_at = datetime.now(timezone.utc)
    _audit(db, actor, "visit", visit.id, "visit.updated", {"fields": sorted(clean)})
    db.commit()
    return visit


def photos_of(db: Session, visit_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[VisitPhoto]]:
    """The photos of several visits in one query, oldest first."""
    found: dict[uuid.UUID, list[VisitPhoto]] = {vid: [] for vid in visit_ids}
    if visit_ids:
        rows = db.execute(select(VisitPhoto).where(VisitPhoto.visit_id.in_(visit_ids))
                          .order_by(VisitPhoto.created_at, VisitPhoto.id)).scalars()
        for photo in rows:
            found[photo.visit_id].append(photo)
    return found


def add_photo(db: Session, actor: Actor, media_root: Path, visit_id: uuid.UUID, data: bytes) -> Visit:
    visit = get(db, visit_id)
    count = db.execute(select(func.count()).select_from(VisitPhoto).where(VisitPhoto.visit_id == visit.id)).scalar_one()
    if count >= MAX_PHOTOS:
        raise ContentConflict("too_many_photos", f"Una visita admite hasta {MAX_PHOTOS} fotos. Quita una para subir otra.")
    path, width, height = private_photos.store(media_root, "visitas", visit.id, data)
    photo = VisitPhoto(visit_id=visit.id, path=path, width=width, height=height, created_by=actor.identity.email,
                       created_at=datetime.now(timezone.utc))  # explicit: the order of photos is the order of upload
    db.add(photo)
    db.flush()
    visit.updated_at = datetime.now(timezone.utc)
    _audit(db, actor, "visit", visit.id, "visit.photo_added", {"photo_id": str(photo.id), "count": count + 1})
    db.commit()
    return visit


def remove_photo(db: Session, actor: Actor, media_root: Path | None, visit_id: uuid.UUID, photo_id: uuid.UUID) -> Visit:
    visit = get(db, visit_id)
    photo = db.get(VisitPhoto, photo_id)
    if photo is None or photo.visit_id != visit.id:
        raise ContentNotFound("not_found", "Esa foto no existe.")
    old = photo.path
    db.delete(photo)
    visit.updated_at = datetime.now(timezone.utc)
    _audit(db, actor, "visit", visit.id, "visit.photo_removed", {"photo_id": str(photo_id)})
    db.commit()
    if media_root is not None:
        private_photos.remove(media_root, old)
    return visit


def delete(db: Session, actor: Actor, media_root: Path | None, visit_id: uuid.UUID) -> None:
    visit = get(db, visit_id)
    old = [photo.path for photo in photos_of(db, [visit.id])[visit.id]]
    _audit(db, actor, "visit", visit.id, "visit.deleted", {"kind": visit.kind, "visited_on": visit.visited_on.isoformat(),
                                                          "photos": len(old)})
    db.delete(visit)
    db.commit()
    if media_root is not None:
        for path in old:
            private_photos.remove(media_root, path)


def photo_file(db: Session, media_root: Path, visit_id: uuid.UUID, photo_id: uuid.UUID) -> Path:
    photo = db.get(VisitPhoto, photo_id)
    path = private_photos.resolve(media_root, photo.path) if photo is not None and photo.visit_id == visit_id else None
    if path is None:
        raise ContentNotFound("not_found", "Esa foto no existe.")
    return path
