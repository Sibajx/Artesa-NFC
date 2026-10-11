"""Gestión → Capacitaciones: training manuals per craft, written and with
drawings. Everything here (reading too) needs the Training permission. Asking for
a review is for whoever holds it; answering the review and deleting a manual are
for the owner."""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.admin.media import _media_root, _read_body, require_upload_guard
from app.api.admin.writes import _fail, actor, require_write_guard
from app.api.deps import get_db
from app.core import permissions as perms
from app.core.access import FORBIDDEN_ERROR, OWNER, require_permission
from app.core.config import get_settings
from app.models.training import TrainingManual
from app.services import private_photos, training
from app.services.content import Actor, ContentError

_NEEDS = [Depends(require_permission(perms.TRAINING))]
reads = APIRouter(prefix="/api/admin/v1/training", tags=["admin", "training"], dependencies=_NEEDS)
writes = APIRouter(prefix="/api/admin/v1/training", tags=["admin", "training"], dependencies=[*_NEEDS, Depends(require_write_guard)])
uploads = APIRouter(prefix="/api/admin/v1/training", tags=["admin", "training"], dependencies=[*_NEEDS, Depends(require_upload_guard)])


class ImageOut(BaseModel):
    id: uuid.UUID
    url: str  # served only to people holding the Training permission
    caption: str | None
    width: int
    height: int


class ManualOut(BaseModel):
    id: uuid.UUID
    title: str
    craft: str
    written: str | None
    visual: str | None
    next_steps: str | None
    status: str
    review_note: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    submitted_at: datetime | None
    images: list[ImageOut]
    created_by: str
    created_at: datetime
    updated_at: datetime


class ManualList(BaseModel):
    data: list[ManualOut]


class ManualBody(BaseModel):
    title: str = Field(max_length=120)
    craft: str = Field(max_length=80)
    written: str | None = Field(default=None, max_length=20000)
    visual: str | None = Field(default=None, max_length=20000)
    next_steps: str | None = Field(default=None, max_length=3000)


class ManualPatch(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    craft: str | None = Field(default=None, max_length=80)
    written: str | None = Field(default=None, max_length=20000)
    visual: str | None = Field(default=None, max_length=20000)
    next_steps: str | None = Field(default=None, max_length=3000)


class ReviewBody(BaseModel):
    decision: Literal["approve", "changes"]
    note: str | None = Field(default=None, max_length=3000)


def _out(db: Session, m: TrainingManual) -> ManualOut:
    images = [ImageOut(id=i.id, url=f"/api/admin/v1/training/images/{i.id}", caption=i.caption, width=i.width, height=i.height)
              for i in training.images_of(db, m.id)]
    return ManualOut(id=m.id, title=m.title, craft=m.craft, written=m.written, visual=m.visual, next_steps=m.next_steps,
                     status=m.status, review_note=m.review_note, reviewed_by=m.reviewed_by, reviewed_at=m.reviewed_at,
                     submitted_at=m.submitted_at, images=images, created_by=m.created_by, created_at=m.created_at,
                     updated_at=m.updated_at)


def _root() -> Path | None:
    s = get_settings()
    return Path(s.media_root) if s.media_enabled else None


def _owner_only(who: Actor) -> None:
    if not who.identity.has(OWNER):
        raise HTTPException(status_code=403, detail=FORBIDDEN_ERROR)


@reads.get("", response_model=ManualList)
def list_manuals(craft: str | None = Query(default=None, max_length=80), db: Session = Depends(get_db)) -> ManualList:
    return ManualList(data=[_out(db, m) for m in training.listing(db, craft)])


@reads.get("/images/{image_id}")
def manual_image(image_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    root = _root()
    try:
        if root is None:
            raise training.ContentNotFound("not_found", "Ese dibujo no existe.")
        path = training.image_file(db, root, image_id)
    except ContentError as exc:
        raise _fail(exc) from None
    return FileResponse(path, media_type="image/jpeg", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})


@reads.get("/{manual_id}", response_model=ManualOut)
def manual_detail(manual_id: uuid.UUID, db: Session = Depends(get_db)) -> ManualOut:
    try:
        return _out(db, training.get(db, manual_id))
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("", status_code=201, response_model=ManualOut)
def create_manual(body: ManualBody, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> ManualOut:
    try:
        return _out(db, training.create(db, who, body.model_dump()))
    except ContentError as exc:
        raise _fail(exc) from None


@writes.patch("/{manual_id}", response_model=ManualOut)
def update_manual(manual_id: uuid.UUID, body: ManualPatch, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> ManualOut:
    try:
        return _out(db, training.update(db, who, manual_id, body.model_dump(exclude_unset=True)))
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("/{manual_id}/submit", response_model=ManualOut)
def submit_manual(manual_id: uuid.UUID, background: BackgroundTasks, who: Actor = Depends(actor),
                  db: Session = Depends(get_db)) -> ManualOut:
    try:
        manual = training.submit(db, who, manual_id)
        background.add_task(training.notify_submitted, manual.title, manual.craft, who.identity.email)
        return _out(db, manual)
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("/{manual_id}/review", response_model=ManualOut)
def review_manual(manual_id: uuid.UUID, body: ReviewBody, background: BackgroundTasks, who: Actor = Depends(actor),
                  db: Session = Depends(get_db)) -> ManualOut:
    _owner_only(who)
    try:
        manual = training.review(db, who, manual_id, body.decision, body.note)
        background.add_task(training.notify_reviewed, manual.title, body.decision, manual.review_note, manual.created_by)
        return _out(db, manual)
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("/images/{image_id}/delete", response_model=ManualOut)
def delete_image(image_id: uuid.UUID, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> ManualOut:
    try:
        return _out(db, training.remove_image(db, who, _root(), image_id))
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("/{manual_id}/delete", status_code=204)
def delete_manual(manual_id: uuid.UUID, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> Response:
    _owner_only(who)
    try:
        training.delete(db, who, _root(), manual_id)
    except ContentError as exc:
        raise _fail(exc) from None
    return Response(status_code=204)


@uploads.post("/{manual_id}/images", status_code=201, response_model=ManualOut)
async def upload_image(manual_id: uuid.UUID, request: Request, caption: str | None = Query(default=None, max_length=200),
                       who: Actor = Depends(actor), db: Session = Depends(get_db)) -> ManualOut:
    root = _media_root()
    data = await _read_body(request, private_photos.MAX_PHOTO_BYTES)
    try:
        await run_in_threadpool(training.add_image, db, who, root, manual_id, data, caption)
        return _out(db, training.get(db, manual_id))
    except ContentError as exc:
        raise _fail(exc) from None
