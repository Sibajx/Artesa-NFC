"""P-028: Gestión → Hero (the PO and the partner with the ``hero`` role).

Reads and ordinary writes follow the other admin routers (Access identity,
CSRF header, JSON). The video upload sends the file as the body with its own
content type, like the media upload. Only ``hero`` accounts and the owner pass
``require_hero``; everyone else gets 403.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.admin.writes import ADMIN_WRITE_HEADER, CSRF_ERROR, _fail, _same_origin, actor, require_write_guard
from app.api.deps import get_db
from app.core.access import FORBIDDEN_ERROR, HERO, AdminIdentity, require_admin
from app.core.config import get_settings
from app.services import hero
from app.services.content import Actor, ContentError

UPLOAD_TYPES = frozenset({"video/mp4", "video/webm", "video/quicktime", "application/octet-stream"})
UPLOAD_TYPE_ERROR = {"code": "unsupported_media_type", "message": "Send the video as the body with its own content type."}


def require_hero(identity: AdminIdentity = Depends(require_admin)) -> AdminIdentity:
    if not identity.has(HERO):
        raise HTTPException(status_code=403, detail=FORBIDDEN_ERROR)
    return identity


def require_upload_guard(request: Request) -> None:
    if request.headers.get(ADMIN_WRITE_HEADER) != "1" or not _same_origin(request):
        raise HTTPException(status_code=403, detail=CSRF_ERROR)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() not in UPLOAD_TYPES:
        raise HTTPException(status_code=415, detail=UPLOAD_TYPE_ERROR)


TOO_LARGE_ERROR = {"code": "too_large", "message": "El video pesa más de 200 MB."}


async def _spool_body(request: Request, folder: Path, limit: int) -> tuple[Path, int]:
    """Writes the request body to a private file in ``folder`` as it arrives (0600), so a
    200 MB video never sits in memory. Removes the file if anything goes wrong."""
    declared = request.headers.get("content-length")
    if declared and declared.isascii() and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=TOO_LARGE_ERROR)
    path = folder / f".incoming-{uuid.uuid4().hex}.part"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    received = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            async for chunk in request.stream():
                received += len(chunk)
                if received > limit:
                    raise HTTPException(status_code=413, detail=TOO_LARGE_ERROR)
                await run_in_threadpool(handle.write, chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, received


class CampaignOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    is_default: bool
    start_month: int | None
    start_day: int | None
    end_month: int | None
    end_day: int | None
    # no_video | processing | error | draft | live | scheduled
    status: str
    error: str | None
    published: bool
    forced: bool
    forced_until: date | None
    video_mp4: str | None
    video_webm: str | None
    poster: str | None
    updated_by: str | None
    updated_at: datetime


class HeroState(BaseModel):
    today: date
    ffmpeg_available: bool
    media_enabled: bool
    live_id: uuid.UUID | None
    # forced | date | default
    live_reason: str | None
    campaigns: list[CampaignOut]


def _state(db: Session) -> HeroState:
    campaigns = hero.all_campaigns(db)
    today = hero.today_mx()
    live, reason = hero.active(campaigns, today)
    settings = get_settings()
    return HeroState(
        today=today, ffmpeg_available=hero.ffmpeg_available(), media_enabled=settings.media_enabled,
        live_id=live.id if live else None, live_reason=reason,
        campaigns=[CampaignOut(
            id=c.id, slug=c.slug, name=c.name, is_default=c.is_default, start_month=c.start_month,
            start_day=c.start_day, end_month=c.end_month, end_day=c.end_day, status=hero.status_of(c, live),
            error=hero.error_of(c), published=c.published,
            forced=c.forced and (c.forced_until is None or today <= c.forced_until), forced_until=c.forced_until,
            video_mp4=c.video_mp4, video_webm=c.video_webm, poster=c.poster, updated_by=c.updated_by,
            updated_at=c.updated_at) for c in campaigns],
    )


class DayRange(BaseModel):
    start_month: int = Field(ge=1, le=12)
    start_day: int = Field(ge=1, le=31)
    end_month: int = Field(ge=1, le=12)
    end_day: int = Field(ge=1, le=31)


class CampaignCreate(DayRange):
    name: str = Field(max_length=200)


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    start_month: int | None = Field(default=None, ge=1, le=12)
    start_day: int | None = Field(default=None, ge=1, le=31)
    end_month: int | None = Field(default=None, ge=1, le=12)
    end_day: int | None = Field(default=None, ge=1, le=31)


class ForceBody(BaseModel):
    campaign_id: uuid.UUID
    until: date | None = None


class Empty(BaseModel):
    pass


reads = APIRouter(prefix="/api/admin/v1/hero", tags=["admin", "hero"], dependencies=[Depends(require_hero)])
writes = APIRouter(prefix="/api/admin/v1/hero", tags=["admin", "hero"],
                   dependencies=[Depends(require_hero), Depends(require_write_guard)])
uploads = APIRouter(prefix="/api/admin/v1/hero", tags=["admin", "hero"],
                    dependencies=[Depends(require_hero), Depends(require_upload_guard)])


def _public_root() -> Path | None:
    settings = get_settings()
    return settings.media_public_dir if settings.media_enabled else None


@reads.get("", response_model=HeroState)
def get_state(db: Session = Depends(get_db)) -> HeroState:
    return _state(db)


@writes.post("/campaigns", response_model=HeroState, status_code=201)
def create_campaign(body: CampaignCreate, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> HeroState:
    try:
        hero.create(db, who, body.name, (body.start_month, body.start_day), (body.end_month, body.end_day))
    except ContentError as exc:
        raise _fail(exc) from None
    return _state(db)


@writes.patch("/campaigns/{campaign_id}", response_model=HeroState)
def update_campaign(campaign_id: uuid.UUID, body: CampaignUpdate, who: Actor = Depends(actor),
                    db: Session = Depends(get_db)) -> HeroState:
    parts = (body.start_month, body.start_day, body.end_month, body.end_day)
    if any(p is not None for p in parts) and any(p is None for p in parts):
        raise HTTPException(status_code=422, detail={"code": "invalid_date", "message": "Indica el inicio y el fin.",
                                                     "field": "start"})
    span = ((parts[0], parts[1]), (parts[2], parts[3])) if parts[0] is not None else (None, None)
    try:
        hero.update(db, who, campaign_id, body.name, span[0], span[1])
    except ContentError as exc:
        raise _fail(exc) from None
    return _state(db)


@writes.delete("/campaigns/{campaign_id}", response_model=HeroState)
def delete_campaign(campaign_id: uuid.UUID, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> HeroState:
    try:
        hero.delete(db, who, _public_root(), campaign_id)
    except ContentError as exc:
        raise _fail(exc) from None
    return _state(db)


@writes.post("/campaigns/{campaign_id}/publish", response_model=HeroState)
def publish_campaign(campaign_id: uuid.UUID, body: Empty, who: Actor = Depends(actor),
                     db: Session = Depends(get_db)) -> HeroState:
    try:
        hero.publish(db, who, campaign_id, True)
    except ContentError as exc:
        raise _fail(exc) from None
    return _state(db)


@writes.post("/campaigns/{campaign_id}/unpublish", response_model=HeroState)
def unpublish_campaign(campaign_id: uuid.UUID, body: Empty, who: Actor = Depends(actor),
                       db: Session = Depends(get_db)) -> HeroState:
    try:
        hero.publish(db, who, campaign_id, False)
    except ContentError as exc:
        raise _fail(exc) from None
    return _state(db)


@writes.post("/force", response_model=HeroState)
def force_campaign(body: ForceBody, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> HeroState:
    try:
        hero.force(db, who, body.campaign_id, body.until)
    except ContentError as exc:
        raise _fail(exc) from None
    return _state(db)


@writes.delete("/force", response_model=HeroState)
def unforce_campaign(who: Actor = Depends(actor), db: Session = Depends(get_db)) -> HeroState:
    hero.unforce(db, who)
    return _state(db)


@uploads.post("/campaigns/{campaign_id}/video", response_model=HeroState, status_code=202)
async def upload_video(campaign_id: uuid.UUID, request: Request, background: BackgroundTasks,
                       start: float = Query(default=0, ge=0, le=hero.MAX_START_SECONDS),
                       who: Actor = Depends(actor), db: Session = Depends(get_db)) -> HeroState:
    settings = get_settings()
    if not settings.media_enabled:
        raise HTTPException(status_code=503, detail={"code": "media_unavailable",
                                                     "message": "El servidor no tiene carpeta de medios configurada."})
    try:
        hero.ensure_ffmpeg()
    except ContentError as exc:
        raise _fail(exc) from None
    media_root = Path(settings.media_root)
    spooled, size = await _spool_body(request, await run_in_threadpool(hero.originals_dir, media_root),
                                      hero.MAX_ORIGINAL_BYTES)
    try:
        original = await run_in_threadpool(hero.begin_upload, db, who, media_root, campaign_id, spooled, size, start)
    except ContentError as exc:
        spooled.unlink(missing_ok=True)
        raise _fail(exc) from None
    except BaseException:
        spooled.unlink(missing_ok=True)
        raise
    background.add_task(hero.run_job, campaign_id, settings.media_public_dir, original, start)
    return _state(db)
