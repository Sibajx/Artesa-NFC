"""Visit log: visits to artisans and galleries. The written summary is
mandatory (what was said); the photo is one and optional, stored privately."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.artisan import Artisan
from app.models.visit import VISIT_KINDS, Visit
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
    return db.execute(select(Artisan.full_name).where(Artisan.id == visit.artisan_id)).scalar_one_or_none() \
        if visit.artisan_id else None


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
    for key, limit in (("place", 200), ("attendees", 300), ("agreements", 3000)):
        if key in data:
            out[key] = _clean(data[key], limit)
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
                                                          "artisan_id": str(visit.artisan_id) if visit.artisan_id else None})
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


def set_photo(db: Session, actor: Actor, media_root: Path, visit_id: uuid.UUID, data: bytes) -> Visit:
    visit = get(db, visit_id)
    path, width, height = private_photos.store(media_root, "visitas", visit.id, data)
    old = visit.photo_path
    visit.photo_path, visit.photo_width, visit.photo_height = path, width, height
    visit.updated_at = datetime.now(timezone.utc)
    _audit(db, actor, "visit", visit.id, "visit.photo_set", {"replaced": old is not None})
    db.commit()
    if old:
        private_photos.remove(media_root, old)
    return visit


def remove_photo(db: Session, actor: Actor, media_root: Path | None, visit_id: uuid.UUID) -> Visit:
    visit = get(db, visit_id)
    old = visit.photo_path
    if old is None:
        raise ContentConflict("no_photo", "Esta visita no tiene foto.")
    visit.photo_path = visit.photo_width = visit.photo_height = None
    visit.updated_at = datetime.now(timezone.utc)
    _audit(db, actor, "visit", visit.id, "visit.photo_removed", {})
    db.commit()
    if media_root is not None:
        private_photos.remove(media_root, old)
    return visit


def delete(db: Session, actor: Actor, media_root: Path | None, visit_id: uuid.UUID) -> None:
    visit = get(db, visit_id)
    old = visit.photo_path
    _audit(db, actor, "visit", visit.id, "visit.deleted", {"kind": visit.kind, "visited_on": visit.visited_on.isoformat()})
    db.delete(visit)
    db.commit()
    if old and media_root is not None:
        private_photos.remove(media_root, old)


def photo_file(db: Session, media_root: Path, visit_id: uuid.UUID) -> Path:
    visit = get(db, visit_id)
    path = private_photos.resolve(media_root, visit.photo_path) if visit.photo_path else None
    if path is None:
        raise ContentNotFound("not_found", "Esa foto no existe.")
    return path
