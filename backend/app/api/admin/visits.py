"""Gestión → Visitas: the visit log of artisans and galleries. Everything here
(reading too) needs the Visits permission, which only Sol and Hariel hold, plus
the owner. Deleting a visit is for the owner."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.admin.media import _media_root, _read_body, require_upload_guard
from app.api.admin.writes import _fail, actor, require_write_guard
from app.api.deps import get_db
from app.core import permissions as perms
from app.core.access import FORBIDDEN_ERROR, OWNER, AdminIdentity, require_admin, require_permission
from app.core.config import get_settings
from app.models.visit import Visit
from app.services import private_photos, visits
from app.services.content import Actor, ContentError

_NEEDS = [Depends(require_permission(perms.VISITS))]
reads = APIRouter(prefix="/api/admin/v1/visits", tags=["admin", "visits"], dependencies=_NEEDS)
writes = APIRouter(prefix="/api/admin/v1/visits", tags=["admin", "visits"], dependencies=[*_NEEDS, Depends(require_write_guard)])
uploads = APIRouter(prefix="/api/admin/v1/visits", tags=["admin", "visits"], dependencies=[*_NEEDS, Depends(require_upload_guard)])


class VisitOut(BaseModel):
    id: uuid.UUID
    visited_on: date
    kind: str
    artisan_id: uuid.UUID | None
    artisan_name: str | None
    place: str | None
    attendees: str | None
    summary: str
    agreements: str | None
    consent_to_publish: bool
    photo: str | None  # URL, served only to people holding the Visits permission
    recorded_by: str
    created_at: datetime
    updated_at: datetime


class VisitList(BaseModel):
    data: list[VisitOut]


class VisitBody(BaseModel):
    visited_on: date
    kind: Literal["artesano", "galeria", "otro"]
    artisan_id: uuid.UUID | None = None
    place: str | None = Field(default=None, max_length=200)
    attendees: str | None = Field(default=None, max_length=300)
    summary: str = Field(max_length=5000)
    agreements: str | None = Field(default=None, max_length=3000)
    consent_to_publish: bool = False


class VisitPatch(BaseModel):
    visited_on: date | None = None
    kind: Literal["artesano", "galeria", "otro"] | None = None
    artisan_id: uuid.UUID | None = None
    place: str | None = Field(default=None, max_length=200)
    attendees: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None, max_length=5000)
    agreements: str | None = Field(default=None, max_length=3000)
    consent_to_publish: bool | None = None


def _out(v: Visit, name: str | None) -> VisitOut:
    return VisitOut(id=v.id, visited_on=v.visited_on, kind=v.kind, artisan_id=v.artisan_id, artisan_name=name, place=v.place,
                    attendees=v.attendees, summary=v.summary, agreements=v.agreements, consent_to_publish=v.consent_to_publish,
                    photo=f"/api/admin/v1/visits/{v.id}/photo" if v.photo_path else None, recorded_by=v.recorded_by,
                    created_at=v.created_at, updated_at=v.updated_at)


def _one(db: Session, visit: Visit) -> VisitOut:
    return _out(visit, visits.artisan_name(db, visit))


def _root() -> Path | None:
    s = get_settings()
    return Path(s.media_root) if s.media_enabled else None


@reads.get("", response_model=VisitList)
def list_visits(artisan_id: uuid.UUID | None = None, db: Session = Depends(get_db)) -> VisitList:
    return VisitList(data=[_out(v, name) for v, name in visits.listing(db, artisan_id)])


@reads.get("/{visit_id}", response_model=VisitOut)
def visit_detail(visit_id: uuid.UUID, db: Session = Depends(get_db)) -> VisitOut:
    try:
        return _one(db, visits.get(db, visit_id))
    except ContentError as exc:
        raise _fail(exc) from None


@reads.get("/{visit_id}/photo")
def visit_photo(visit_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    root = _root()
    try:
        if root is None:
            raise visits.ContentNotFound("not_found", "Esa foto no existe.")
        path = visits.photo_file(db, root, visit_id)
    except ContentError as exc:
        raise _fail(exc) from None
    return FileResponse(path, media_type="image/jpeg", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})


@writes.post("", status_code=201, response_model=VisitOut)
def create_visit(body: VisitBody, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> VisitOut:
    try:
        return _one(db, visits.create(db, who, body.model_dump()))
    except ContentError as exc:
        raise _fail(exc) from None


@writes.patch("/{visit_id}", response_model=VisitOut)
def update_visit(visit_id: uuid.UUID, body: VisitPatch, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> VisitOut:
    try:
        return _one(db, visits.update(db, who, visit_id, body.model_dump(exclude_unset=True)))
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("/{visit_id}/photo/delete", response_model=VisitOut)
def delete_visit_photo(visit_id: uuid.UUID, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> VisitOut:
    try:
        return _one(db, visits.remove_photo(db, who, _root(), visit_id))
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("/{visit_id}/delete", status_code=204)
def delete_visit(visit_id: uuid.UUID, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> Response:
    if not who.identity.has(OWNER):
        raise HTTPException(status_code=403, detail=FORBIDDEN_ERROR)
    try:
        visits.delete(db, who, _root(), visit_id)
    except ContentError as exc:
        raise _fail(exc) from None
    return Response(status_code=204)


@uploads.post("/{visit_id}/photo", status_code=201, response_model=VisitOut)
async def upload_visit_photo(visit_id: uuid.UUID, request: Request, who: Actor = Depends(actor),
                             db: Session = Depends(get_db)) -> VisitOut:
    root = _media_root()
    data = await _read_body(request, private_photos.MAX_PHOTO_BYTES)
    try:
        visit = await run_in_threadpool(visits.set_photo, db, who, root, visit_id, data)
        return _one(db, visit)
    except ContentError as exc:
        raise _fail(exc) from None
