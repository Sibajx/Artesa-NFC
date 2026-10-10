"""Production follow-up of a piece: marking steps, private photos, and the
board Hariel works from. ``chip_programmed`` (and a delivery already recorded
as the piece's location) come from data that already exists, so they are shown
as done without anyone typing them twice."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.artisan import Artisan
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.models.piece import Piece
from app.models.production import MANUAL_STEPS, PRODUCTION_STEPS, ProductionPhoto, ProductionStep
from app.services import locations as locations_service
from app.services import private_photos
from app.services.content import Actor, ContentConflict, ContentNotFound, _audit

MAX_PHOTOS_PER_STEP = 5
BUCKETS = ("pending", "in_process", "ready_to_ship", "in_transit", "delivered")


@dataclass
class StepView:
    step: str
    done: bool
    source: str | None = None  # "manual" | "auto"
    done_at: datetime | None = None
    done_by: str | None = None
    note: str | None = None
    carrier: str | None = None
    tracking: str | None = None
    photo_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass(frozen=True)
class BoardCard:
    piece_id: uuid.UUID
    name: str
    public_code: str
    artisan_name: str
    bucket: str
    done: int
    total: int
    last_step: str | None
    last_done_at: datetime | None


def _piece(db: Session, piece_id: uuid.UUID) -> Piece:
    piece = db.get(Piece, piece_id)
    if piece is None or piece.trashed_at is not None:
        raise ContentNotFound("not_found", "Esa pieza no existe.")
    return piece


def _views(db: Session, piece_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[StepView]]:
    """For each piece, its six steps in order, manual and automatic merged."""
    rows = db.execute(select(ProductionStep).where(ProductionStep.piece_id.in_(piece_ids))).scalars().all() if piece_ids else []
    photos: dict[uuid.UUID, list[uuid.UUID]] = {}
    if rows:
        for photo in db.execute(select(ProductionPhoto).where(ProductionPhoto.step_id.in_([r.id for r in rows]))
                                .order_by(ProductionPhoto.created_at)).scalars():
            photos.setdefault(photo.step_id, []).append(photo.id)
    manual = {(r.piece_id, r.step): r for r in rows}
    chips = {}
    where = locations_service.current_by_piece(db, piece_ids)
    if piece_ids:
        for tag in db.execute(select(NfcTag).where(NfcTag.piece_id.in_(piece_ids),
                                                   NfcTag.status.in_((NfcTagStatus.programmed, NfcTagStatus.locked)))).scalars():
            chips[tag.piece_id] = tag
    out: dict[uuid.UUID, list[StepView]] = {}
    for pid in piece_ids:
        steps = []
        for key in PRODUCTION_STEPS:
            row = manual.get((pid, key))
            if row is not None:
                steps.append(StepView(key, True, "manual", row.done_at, row.done_by, row.note, row.carrier, row.tracking,
                                      photos.get(row.id, [])))
            elif key == "chip_programmed" and pid in chips:
                tag = chips[pid]
                steps.append(StepView(key, True, "auto", tag.programmed_at or tag.created_at))
            elif key == "delivered" and pid in where and where[pid].location == "entregada":
                steps.append(StepView(key, True, "auto", where[pid].created_at))
            else:
                steps.append(StepView(key, False))
        out[pid] = steps
    return out


def timeline(db: Session, piece_id: uuid.UUID) -> tuple[Piece, list[StepView]]:
    piece = _piece(db, piece_id)
    return piece, _views(db, [piece.id])[piece.id]


def _bucket(steps: list[StepView]) -> str:
    done = {s.step for s in steps if s.done}
    if "delivered" in done:
        return "delivered"
    if "shipped" in done:
        return "in_transit"
    if "packed" in done:
        return "ready_to_ship"
    return "in_process" if done else "pending"


def board(db: Session) -> list[BoardCard]:
    rows = db.execute(select(Piece, Artisan.full_name).join(Artisan, Artisan.id == Piece.artisan_id)
                      .where(Piece.trashed_at.is_(None)).order_by(Artisan.full_name, Piece.public_code)).all()
    views = _views(db, [p.id for p, _ in rows])
    cards = []
    for piece, artisan in rows:
        steps = views[piece.id]
        done = [s for s in steps if s.done]
        # The furthest step along the pipeline (the order is the story: chip, pack, ship, deliver).
        last = done[-1] if done else None
        cards.append(BoardCard(piece.id, piece.name, piece.public_code, artisan, _bucket(steps), len(done), len(steps),
                               last.step if last else None, last.done_at if last else None))
    return cards


def mark(db: Session, actor: Actor, piece_id: uuid.UUID, step: str, note: str | None, carrier: str | None,
         tracking: str | None) -> None:
    piece = _piece(db, piece_id)
    if step == "chip_programmed":
        raise ContentConflict("automatic_step", "Este paso se marca solo cuando el chip se programa en Certificación.")
    if step not in MANUAL_STEPS:
        raise ContentNotFound("not_found", "Ese paso no existe.")
    note, carrier, tracking = ((v or "").strip()[:500] or None for v in (note, carrier, tracking))
    if step != "shipped" and (carrier or tracking):
        raise ContentConflict("invalid_step", "La paquetería y la guía solo van en el envío.", "carrier")
    db.add(ProductionStep(piece_id=piece.id, step=step, done_by=actor.identity.email, note=note, done_at=func.clock_timestamp(),
                          carrier=carrier[:80] if carrier else None, tracking=tracking[:80] if tracking else None))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ContentConflict("already_done", "Ese paso ya está marcado.") from None
    _audit(db, actor, "piece", piece.id, "piece.production_step", {"step": step, "carrier": carrier, "tracking": tracking})
    db.commit()


def _step_row(db: Session, piece_id: uuid.UUID, step: str) -> ProductionStep:
    row = db.execute(select(ProductionStep).where(ProductionStep.piece_id == piece_id,
                                                  ProductionStep.step == step)).scalar_one_or_none()
    if row is None:
        raise ContentConflict("step_not_done", "Primero marca ese paso.")
    return row


def unmark(db: Session, actor: Actor, media_root: Path | None, piece_id: uuid.UUID, step: str) -> None:
    piece = _piece(db, piece_id)
    row = _step_row(db, piece.id, step)
    paths = [p.path for p in db.execute(select(ProductionPhoto).where(ProductionPhoto.step_id == row.id)).scalars()]
    db.delete(row)
    _audit(db, actor, "piece", piece.id, "piece.production_step_undone", {"step": step, "photos": len(paths)})
    db.commit()
    if media_root is not None:
        for path in paths:
            private_photos.remove(media_root, path)


def add_photo(db: Session, actor: Actor, media_root: Path, piece_id: uuid.UUID, step: str, data: bytes) -> ProductionPhoto:
    piece = _piece(db, piece_id)
    row = _step_row(db, piece.id, step)
    count = len(db.execute(select(ProductionPhoto.id).where(ProductionPhoto.step_id == row.id)).all())
    if count >= MAX_PHOTOS_PER_STEP:
        raise ContentConflict("too_many_photos", f"Cada paso admite hasta {MAX_PHOTOS_PER_STEP} fotos.")
    path, width, height = private_photos.store(media_root, "produccion", piece.id, data)
    photo = ProductionPhoto(step_id=row.id, path=path, width=width, height=height, created_by=actor.identity.email)
    db.add(photo)
    db.flush()
    _audit(db, actor, "piece", piece.id, "piece.production_photo", {"step": step, "photo": str(photo.id)})
    db.commit()
    return photo


def delete_photo(db: Session, actor: Actor, media_root: Path | None, photo_id: uuid.UUID) -> None:
    photo = db.get(ProductionPhoto, photo_id)
    if photo is None:
        raise ContentNotFound("not_found", "Esa foto no existe.")
    step = db.get(ProductionStep, photo.step_id)
    path = photo.path
    db.delete(photo)
    _audit(db, actor, "piece", step.piece_id, "piece.production_photo_deleted", {"step": step.step, "photo": str(photo_id)})
    db.commit()
    if media_root is not None:
        private_photos.remove(media_root, path)


def photo_path(db: Session, media_root: Path, photo_id: uuid.UUID) -> Path:
    photo = db.get(ProductionPhoto, photo_id)
    path = private_photos.resolve(media_root, photo.path) if photo is not None else None
    if path is None:
        raise ContentNotFound("not_found", "Esa foto no existe.")
    return path
