"""Gestión → Producción: where each piece is on its way from the artisan to the
buyer (chip placed, programmed, packed, shipped, delivered), with private
photos. Reading needs "Ver"; marking steps and uploading photos, "Logística"."""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.admin.media import _media_root, _read_body, require_upload_guard
from app.api.admin.writes import _fail, actor, require_write_guard
from app.api.deps import get_db
from app.core import permissions as perms
from app.core.access import require_admin, require_permission
from app.core.config import get_settings
from app.services import private_photos, production
from app.services.content import Actor, ContentError

reads = APIRouter(prefix="/api/admin/v1/production", tags=["admin", "production"], dependencies=[Depends(require_admin)])
writes = APIRouter(prefix="/api/admin/v1/production", tags=["admin", "production"],
                   dependencies=[Depends(require_permission(perms.LOGISTICS)), Depends(require_write_guard)])
uploads = APIRouter(prefix="/api/admin/v1/production", tags=["admin", "production"],
                    dependencies=[Depends(require_permission(perms.LOGISTICS)), Depends(require_upload_guard)])


class StepOut(BaseModel):
    step: str
    done: bool
    source: str | None
    done_at: datetime | None
    done_by: str | None
    note: str | None
    carrier: str | None
    tracking: str | None
    photos: list[str]  # URLs, served only to signed-in admins


class Timeline(BaseModel):
    piece_id: uuid.UUID
    name: str
    public_code: str
    steps: list[StepOut]
    # Whether photos can be stored (MEDIA_ROOT configured).
    photos_enabled: bool


class CardOut(BaseModel):
    piece_id: uuid.UUID
    name: str
    public_code: str
    artisan_name: str
    bucket: str
    done: int
    total: int
    last_step: str | None
    last_done_at: datetime | None


class Board(BaseModel):
    data: list[CardOut]
    counts: dict[str, int]


class StepBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    carrier: str | None = Field(default=None, max_length=80)
    tracking: str | None = Field(default=None, max_length=80)


def _photo_url(photo_id: uuid.UUID) -> str:
    return f"/api/admin/v1/production/photos/{photo_id}"


def _timeline(db: Session, piece_id: uuid.UUID) -> Timeline:
    piece, steps = production.timeline(db, piece_id)
    return Timeline(
        piece_id=piece.id, name=piece.name, public_code=piece.public_code, photos_enabled=get_settings().media_enabled,
        steps=[StepOut(step=s.step, done=s.done, source=s.source, done_at=s.done_at, done_by=s.done_by, note=s.note,
                       carrier=s.carrier, tracking=s.tracking, photos=[_photo_url(p) for p in s.photo_ids]) for s in steps])


@reads.get("/board", response_model=Board)
def board(db: Session = Depends(get_db)) -> Board:
    cards = production.board(db)
    counts = {b: 0 for b in production.BUCKETS}
    for c in cards:
        counts[c.bucket] += 1
    return Board(data=[CardOut(**vars(c)) for c in cards], counts=counts)


@reads.get("/pieces/{piece_id}", response_model=Timeline)
def piece_timeline(piece_id: uuid.UUID, db: Session = Depends(get_db)) -> Timeline:
    try:
        return _timeline(db, piece_id)
    except ContentError as exc:
        raise _fail(exc) from None


@reads.get("/photos/{photo_id}")
def photo(photo_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    root = Path(get_settings().media_root) if get_settings().media_enabled else None
    try:
        if root is None:
            raise production.ContentNotFound("not_found", "Esa foto no existe.")
        path = production.photo_path(db, root, photo_id)
    except ContentError as exc:
        raise _fail(exc) from None
    return FileResponse(path, media_type="image/jpeg", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})


@writes.post("/pieces/{piece_id}/steps/{step}", status_code=201, response_model=Timeline)
def mark_step(piece_id: uuid.UUID, step: str, body: StepBody, who: Actor = Depends(actor),
              db: Session = Depends(get_db)) -> Timeline:
    try:
        production.mark(db, who, piece_id, step, body.note, body.carrier, body.tracking)
        return _timeline(db, piece_id)
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("/pieces/{piece_id}/steps/{step}/undo", response_model=Timeline)
def undo_step(piece_id: uuid.UUID, step: str, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> Timeline:
    root = Path(get_settings().media_root) if get_settings().media_enabled else None
    try:
        production.unmark(db, who, root, piece_id, step)
        return _timeline(db, piece_id)
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("/photos/{photo_id}/delete", response_model=Timeline)
def delete_photo(photo_id: uuid.UUID, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> Timeline:
    from app.models.production import ProductionPhoto, ProductionStep

    root = Path(get_settings().media_root) if get_settings().media_enabled else None
    row = db.get(ProductionPhoto, photo_id)
    step = db.get(ProductionStep, row.step_id) if row is not None else None
    try:
        production.delete_photo(db, who, root, photo_id)
        return _timeline(db, step.piece_id)
    except ContentError as exc:
        raise _fail(exc) from None


@uploads.post("/pieces/{piece_id}/steps/{step}/photos", status_code=201, response_model=Timeline)
async def upload_photo(piece_id: uuid.UUID, step: str, request: Request, who: Actor = Depends(actor),
                       db: Session = Depends(get_db)) -> Timeline:
    root = _media_root()
    data = await _read_body(request, private_photos.MAX_PHOTO_BYTES)
    try:
        await run_in_threadpool(production.add_photo, db, who, root, piece_id, step, data)
        return _timeline(db, piece_id)
    except ContentError as exc:
        raise _fail(exc) from None
