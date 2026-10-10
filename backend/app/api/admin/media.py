"""Gestión admin API, phase 4: media (docs/MEDIA.md, API_CONTRACT.md §14.3).

Uploads send the file itself as the request body (no multipart), with its
real content type, and ``role`` / ``alt_text`` in the query string. The CSRF
guard is the one of the other writes, except that the body is the file: a
cross-site page cannot send ``X-Artesa-Admin`` nor an ``image/*``,
``video/mp4`` or ``model/gltf-binary`` body without a CORS preflight, which
the admin API never grants.

Edits (alt text, position, role) and archive/restore are ordinary JSON writes
with ``If-Match``. DELETE (with ``If-Match``) removes a media item for good,
only when it can never have been public (services/media.py ``may_delete``);
otherwise it answers 409 ``may_have_been_public``.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.admin.router import admin_media
from app.api.admin.writes import (
    ADMIN_WRITE_HEADER,
    CSRF_ERROR,
    _fail,
    _same_origin,
    actor,
    expected_version,
    require_write_guard,
)
from app.api.deps import get_db
from app.core import permissions as perms
from app.core.access import require_admin, require_permission
from app.core.config import get_settings
from app.models.media_asset import MediaRole, MediaType
from app.schemas.admin import AdminMedia
from app.schemas.admin_write import MediaUpdate, TransitionBody, provided
from app.services import media, palette
from app.services.content import Actor, ContentError

UPLOAD_CONTENT_TYPES = frozenset({
    "image/jpeg", "image/png", "image/webp", "video/mp4", "model/gltf-binary", "application/octet-stream",
})
UPLOAD_TYPE_ERROR = {"code": "unsupported_media_type",
                     "message": "Send the file as the body with its own content type."}
TOO_LARGE_ERROR = {"code": "too_large", "message": "The file is larger than 25 MB."}


def require_upload_guard(request: Request) -> None:
    if request.headers.get(ADMIN_WRITE_HEADER) != "1" or not _same_origin(request):
        raise HTTPException(status_code=403, detail=CSRF_ERROR)
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in UPLOAD_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail=UPLOAD_TYPE_ERROR)


def _media_root() -> Path:
    settings = get_settings()
    if not settings.media_enabled:
        raise _fail(media.media_unavailable())
    return Path(settings.media_root)


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isascii() and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=TOO_LARGE_ERROR)
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise HTTPException(status_code=413, detail=TOO_LARGE_ERROR)
        chunks.append(chunk)
    return b"".join(chunks)


upload_router = APIRouter(
    prefix="/api/admin/v1",
    tags=["admin"],
    dependencies=[Depends(require_admin), Depends(require_upload_guard)],
)


@upload_router.post("/{owner}/{owner_id}/media", status_code=201, response_model=AdminMedia, dependencies=[Depends(require_permission(perms.EDIT))])
async def upload_media(
    owner: Literal["artisans", "pieces"],
    owner_id: uuid.UUID,
    request: Request,
    role: MediaRole = Query(),
    alt_text: str | None = Query(default=None, max_length=300),
    who: Actor = Depends(actor),
    db: Session = Depends(get_db),
) -> AdminMedia:
    root = _media_root()
    data = await _read_body(request, media.MAX_UPLOAD_BYTES)
    text = (alt_text or "").strip() or None
    owner_type = "artisan" if owner == "artisans" else "piece"
    try:
        asset = await run_in_threadpool(media.upload, db, who, root, owner_type, owner_id, role, text, data)
    except ContentError as exc:
        raise _fail(exc) from None
    if owner_type == "piece" and asset.media_type == MediaType.image:
        # ADR-030 phase 4: the first photo gives the piece its palette.
        await run_in_threadpool(palette.fill_if_missing, db, who, root, owner_id)
    return admin_media(db, asset)


router = APIRouter(
    prefix="/api/admin/v1",
    tags=["admin"],
    dependencies=[Depends(require_admin), Depends(require_write_guard)],
)


@router.patch("/media/{media_id}", response_model=AdminMedia, dependencies=[Depends(require_permission(perms.EDIT))])
def update_media(media_id: uuid.UUID, body: MediaUpdate, expected: datetime = Depends(expected_version),
                 who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminMedia:
    try:
        asset = media.update(db, who, media_id, expected, provided(body, never_null=("position",)))
    except ContentError as exc:
        raise _fail(exc) from None
    return admin_media(db, asset)


@router.post("/media/{media_id}/{action}", response_model=AdminMedia, dependencies=[Depends(require_permission(perms.EDIT))])
def transition_media(media_id: uuid.UUID, action: media.MediaAction, body: TransitionBody,
                     expected: datetime = Depends(expected_version), who: Actor = Depends(actor),
                     db: Session = Depends(get_db)) -> AdminMedia:
    try:
        asset = media.transition(db, who, media_id, expected, action.value)
    except ContentError as exc:
        raise _fail(exc) from None
    return admin_media(db, asset)


@router.delete("/media/{media_id}", status_code=204, dependencies=[Depends(require_permission(perms.EDIT))])
def delete_media(media_id: uuid.UUID, expected: datetime = Depends(expected_version),
                 who: Actor = Depends(actor), db: Session = Depends(get_db)) -> Response:
    root = _media_root()
    try:
        media.delete(db, who, root, media_id, expected)
    except ContentError as exc:
        raise _fail(exc) from None
    return Response(status_code=204)
