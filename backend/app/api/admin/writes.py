"""Gestión admin API, phase 2: content writes (ADR-029, API_CONTRACT.md §14.2).

Besides the Access identity (require_admin), every write needs:
- the ``X-Artesa-Admin: 1`` header and a JSON body, and an ``Origin`` (when
  the browser sends one) equal to the request's own host. Access adds its
  identity header to any request that carries the user's session cookie, a
  cross-site one included; a cross-site page cannot set a custom header or a
  JSON content type without a CORS preflight, which the admin API never
  grants;
- ``If-Match: <updated_at>`` on every change to an existing record: the
  value the UI loaded. A mismatch is 412; a missing header is 428.
"""
from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.admin import router as reads
from app.api.deps import get_db
from app.core.access import AdminIdentity, require_admin
from app.schemas.admin import AdminArtisanDetail, AdminPieceDetail
from app.schemas.admin_write import (
    ArtisanCreate,
    ArtisanUpdate,
    AvailabilityBody,
    PieceCreate,
    PieceUpdate,
    TransitionBody,
    provided,
)
from app.services import content
from app.services.content import Actor, ContentError

ADMIN_WRITE_HEADER = "X-Artesa-Admin"

CSRF_ERROR = {"code": "forbidden", "message": "This request is not allowed from here."}
MEDIA_TYPE_ERROR = {"code": "unsupported_media_type", "message": "The request body must be JSON."}
PRECONDITION_REQUIRED = {"code": "precondition_required", "message": "If-Match with the record's updated_at is required."}
BAD_PRECONDITION = {"code": "bad_request", "message": "If-Match must be the record's updated_at timestamp."}


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    return origin is None or urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()


def require_write_guard(request: Request) -> None:
    if request.headers.get(ADMIN_WRITE_HEADER) != "1" or not _same_origin(request):
        raise HTTPException(status_code=403, detail=CSRF_ERROR)
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail=MEDIA_TYPE_ERROR)


def expected_version(if_match: str | None = Header(default=None, alias="If-Match")) -> datetime:
    if not if_match:
        raise HTTPException(status_code=428, detail=PRECONDITION_REQUIRED)
    try:
        value = datetime.fromisoformat(if_match.strip().strip('"'))
    except ValueError:
        raise HTTPException(status_code=400, detail=BAD_PRECONDITION) from None
    if value.tzinfo is None:
        raise HTTPException(status_code=400, detail=BAD_PRECONDITION)
    return value


def actor(request: Request, identity: AdminIdentity = Depends(require_admin)) -> Actor:
    # With Uvicorn's --proxy-headers behind cloudflared, client.host is the
    # visitor's address; anything that is not an IP is dropped.
    host = request.client.host if request.client else None
    try:
        ip = str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        ip = None
    return Actor(identity=identity, ip_address=ip)


def _fail(exc: ContentError) -> HTTPException:
    detail = {"code": exc.code, "message": exc.message}
    if exc.field:
        detail["field"] = exc.field
    return HTTPException(status_code=exc.status_code, detail=detail)


router = APIRouter(
    prefix="/api/admin/v1",
    tags=["admin"],
    dependencies=[Depends(require_admin), Depends(require_write_guard)],
)

_ARTISAN_NEVER_NULL = ("full_name", "slug", "languages_public")
_PIECE_NEVER_NULL = ("name", "slug", "public_code", "artisan_id")


@router.post("/artisans", status_code=201, response_model=AdminArtisanDetail)
def create_artisan(body: ArtisanCreate, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminArtisanDetail:
    try:
        artisan = content.create_artisan(db, who, body.model_dump())
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_artisan(artisan.id, db)


@router.patch("/artisans/{artisan_id}", response_model=AdminArtisanDetail)
def update_artisan(artisan_id: uuid.UUID, body: ArtisanUpdate, expected: datetime = Depends(expected_version),
                   who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminArtisanDetail:
    try:
        content.update_artisan(db, who, artisan_id, expected, provided(body, never_null=_ARTISAN_NEVER_NULL))
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_artisan(artisan_id, db)


@router.post("/artisans/{artisan_id}/{action}", response_model=AdminArtisanDetail)
def transition_artisan(artisan_id: uuid.UUID, action: content.TransitionAction, body: TransitionBody,
                       expected: datetime = Depends(expected_version), who: Actor = Depends(actor),
                       db: Session = Depends(get_db)) -> AdminArtisanDetail:
    try:
        content.transition_artisan(db, who, artisan_id, expected, action.value, body.reason)
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_artisan(artisan_id, db)


@router.post("/pieces", status_code=201, response_model=AdminPieceDetail)
def create_piece(body: PieceCreate, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        piece = content.create_piece(db, who, body.model_dump())
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece.id, db)


@router.patch("/pieces/{piece_id}", response_model=AdminPieceDetail)
def update_piece(piece_id: uuid.UUID, body: PieceUpdate, expected: datetime = Depends(expected_version),
                 who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        content.update_piece(db, who, piece_id, expected, provided(body, never_null=_PIECE_NEVER_NULL))
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece_id, db)


@router.post("/pieces/{piece_id}/availability", response_model=AdminPieceDetail)
def set_availability(piece_id: uuid.UUID, body: AvailabilityBody, expected: datetime = Depends(expected_version),
                     who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        content.set_availability(db, who, piece_id, expected, body.availability_status)
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece_id, db)


@router.post("/pieces/{piece_id}/{action}", response_model=AdminPieceDetail)
def transition_piece(piece_id: uuid.UUID, action: content.TransitionAction, body: TransitionBody,
                     expected: datetime = Depends(expected_version), who: Actor = Depends(actor),
                     db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        content.transition_piece(db, who, piece_id, expected, action.value, body.reason)
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece_id, db)
