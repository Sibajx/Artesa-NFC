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

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.admin import router as reads
from app.api.deps import get_db
from app.models.artisan import Artisan
from app.core import permissions as perms
from app.core.access import AdminIdentity, require_admin, require_permission
from app.schemas.admin import AdminArtisanDetail, AdminPieceDetail
from app.schemas.admin_write import (
    ArtisanCreate,
    ArtisanUpdate,
    AvailabilityBody,
    PieceCreate,
    PieceUpdate,
    LocationBody,
    SaleBody,
    SaleCancelBody,
    TransitionBody,
    provided,
)
from pathlib import Path

from app.core.config import get_settings
from app.services import content, locations, palette, sales, trash
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
_PIECE_NEVER_NULL = ("name", "slug", "public_code", "artisan_id", "price_currency")


# --- Papelera (services/trash.py) -------------------------------------------------------
# Declared before the generic /{action} transitions so these paths win.


def _media_root_or_none() -> Path | None:
    settings = get_settings()
    return Path(settings.media_root) if settings.media_enabled else None


def _trash_routes(kind: str, plural: str, read, model):
    @router.post(f"/{plural}/{{entity_id}}/trash", response_model=model,
                 name=f"trash_{kind}", dependencies=[Depends(require_permission(perms.EDIT))])
    def trash_entity(entity_id: uuid.UUID, body: TransitionBody, expected: datetime = Depends(expected_version),
                     who: Actor = Depends(actor), db: Session = Depends(get_db)):
        try:
            trash.trash(db, who, kind, entity_id, expected)
        except ContentError as exc:
            raise _fail(exc) from None
        return read(entity_id, db, who.identity)

    @router.post(f"/{plural}/{{entity_id}}/untrash", response_model=model,
                 name=f"untrash_{kind}", dependencies=[Depends(require_permission(perms.EDIT))])
    def untrash_entity(entity_id: uuid.UUID, body: TransitionBody, expected: datetime = Depends(expected_version),
                       who: Actor = Depends(actor), db: Session = Depends(get_db)):
        try:
            trash.untrash(db, who, kind, entity_id, expected)
        except ContentError as exc:
            raise _fail(exc) from None
        return read(entity_id, db, who.identity)

    @router.post(f"/{plural}/{{entity_id}}/purge", status_code=204, name=f"purge_{kind}",
                 dependencies=[Depends(require_permission(perms.EDIT))])
    def purge_entity(entity_id: uuid.UUID, body: TransitionBody, expected: datetime = Depends(expected_version),
                     who: Actor = Depends(actor), db: Session = Depends(get_db)) -> Response:
        try:
            trash.purge(db, who, _media_root_or_none(), kind, entity_id, expected)
        except ContentError as exc:
            raise _fail(exc) from None
        return Response(status_code=204)


_trash_routes("artisan", "artisans", reads.get_artisan, AdminArtisanDetail)
_trash_routes("piece", "pieces", reads.get_piece, AdminPieceDetail)


@router.post("/artisans", status_code=201, response_model=AdminArtisanDetail, dependencies=[Depends(require_permission(perms.EDIT))])
def create_artisan(body: ArtisanCreate, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminArtisanDetail:
    try:
        artisan = content.create_artisan(db, who, body.model_dump())
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_artisan(artisan.id, db)


@router.patch("/artisans/{artisan_id}", response_model=AdminArtisanDetail, dependencies=[Depends(require_permission(perms.EDIT))])
def update_artisan(artisan_id: uuid.UUID, body: ArtisanUpdate, expected: datetime = Depends(expected_version),
                   who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminArtisanDetail:
    try:
        content.update_artisan(db, who, artisan_id, expected, provided(body, never_null=_ARTISAN_NEVER_NULL))
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_artisan(artisan_id, db)


@router.post("/artisans/{artisan_id}/{action}", response_model=AdminArtisanDetail, dependencies=[Depends(require_permission(perms.PUBLISH))])
def transition_artisan(artisan_id: uuid.UUID, action: content.TransitionAction, body: TransitionBody,
                       expected: datetime = Depends(expected_version), who: Actor = Depends(actor),
                       db: Session = Depends(get_db)) -> AdminArtisanDetail:
    try:
        content.transition_artisan(db, who, artisan_id, expected, action.value, body.reason)
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_artisan(artisan_id, db)


@router.post("/pieces", status_code=201, response_model=AdminPieceDetail, dependencies=[Depends(require_permission(perms.EDIT))])
def create_piece(body: PieceCreate, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        piece = content.create_piece(db, who, body.model_dump())
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece.id, db, who.identity)


@router.patch("/pieces/{piece_id}", response_model=AdminPieceDetail, dependencies=[Depends(require_permission(perms.EDIT))])
def update_piece(piece_id: uuid.UUID, body: PieceUpdate, expected: datetime = Depends(expected_version),
                 who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        content.update_piece(db, who, piece_id, expected, provided(body, never_null=_PIECE_NEVER_NULL))
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece_id, db, who.identity)


@router.post("/pieces/{piece_id}/availability", response_model=AdminPieceDetail, dependencies=[Depends(require_permission(perms.PUBLISH))])
def set_availability(piece_id: uuid.UUID, body: AvailabilityBody, expected: datetime = Depends(expected_version),
                     who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        content.set_availability(db, who, piece_id, expected, body.availability_status)
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece_id, db, who.identity)


# P-026 G3: the artisan's authorization to publish. Declared before the
# generic /artisans/{id}/{action} transition.
class AuthorizationNote(BaseModel):
    note: str = Field(min_length=5, max_length=500)


class AuthorizationLink(BaseModel):
    """The only response that carries the authorization link (shown once)."""
    url: str
    whatsapp: str | None
    contact_name: str | None
    artisan_name: str


@router.post("/artisans/{artisan_id}/authorization/request", response_model=AuthorizationLink, dependencies=[Depends(require_permission(perms.AUTHORIZATION))])
def request_authorization(artisan_id: uuid.UUID, who: Actor = Depends(actor),
                          db: Session = Depends(get_db)) -> AuthorizationLink:
    from app.services import authorizations

    try:
        _row, token = authorizations.request(db, who, artisan_id)
    except ContentError as exc:
        raise _fail(exc) from None
    artisan = db.get(Artisan, artisan_id)
    return AuthorizationLink(url=authorizations.link(token), whatsapp=artisan.validation_whatsapp,
                             contact_name=artisan.validation_contact_name,
                             artisan_name=artisan.artistic_name or artisan.full_name)


@router.post("/artisans/{artisan_id}/authorization/record", response_model=AdminArtisanDetail, dependencies=[Depends(require_permission(perms.AUTHORIZATION))])
def record_authorization(artisan_id: uuid.UUID, body: AuthorizationNote, who: Actor = Depends(actor),
                         db: Session = Depends(get_db)) -> AdminArtisanDetail:
    from app.services import authorizations

    try:
        authorizations.record_in_person(db, who, artisan_id, body.note)
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_artisan(artisan_id, db, who.identity)


@router.post("/artisans/{artisan_id}/authorization/revoke", response_model=AdminArtisanDetail, dependencies=[Depends(require_permission(perms.AUTHORIZATION))])
def revoke_authorization(artisan_id: uuid.UUID, body: AuthorizationNote, who: Actor = Depends(actor),
                         db: Session = Depends(get_db)) -> AdminArtisanDetail:
    from app.services import authorizations

    try:
        authorizations.revoke(db, who, artisan_id, body.note)
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_artisan(artisan_id, db, who.identity)


# P-026 G1. Declared before the generic /{action} transition.
@router.post("/pieces/{piece_id}/sale", response_model=AdminPieceDetail, dependencies=[Depends(require_permission(perms.SALES))])
def register_sale(piece_id: uuid.UUID, body: SaleBody, expected: datetime = Depends(expected_version),
                  who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        sales.register(db, who, piece_id, expected, body.model_dump())
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece_id, db, who.identity)


@router.post("/pieces/{piece_id}/sale/cancel", response_model=AdminPieceDetail, dependencies=[Depends(require_permission(perms.SALES))])
def cancel_sale(piece_id: uuid.UUID, body: SaleCancelBody, expected: datetime = Depends(expected_version),
                who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        sales.cancel(db, who, piece_id, expected, body.reason)
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece_id, db, who.identity)


# P-026 G12. Declared before the generic /{action} transition.
@router.post("/pieces/{piece_id}/location", response_model=AdminPieceDetail, dependencies=[Depends(require_permission(perms.LOGISTICS))])
def move_piece(piece_id: uuid.UUID, body: LocationBody, expected: datetime = Depends(expected_version),
               who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        locations.move(db, who, piece_id, expected, body.model_dump())
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece_id, db, who.identity)


class PaletteBody(BaseModel):
    colors: list[str] = Field(max_length=8)


# ADR-030 phase 4. Declared before the generic /{action} transition.
@router.post("/pieces/{piece_id}/palette/generate", response_model=AdminPieceDetail, dependencies=[Depends(require_permission(perms.EDIT))])
def generate_palette(piece_id: uuid.UUID, expected: datetime = Depends(expected_version),
                     who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        palette.generate(db, who, _media_root_or_none(), piece_id, expected)
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece_id, db, who.identity)


@router.post("/pieces/{piece_id}/palette", response_model=AdminPieceDetail, dependencies=[Depends(require_permission(perms.EDIT))])
def set_palette(piece_id: uuid.UUID, body: PaletteBody, expected: datetime = Depends(expected_version),
                who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        palette.set_manual(db, who, piece_id, expected, body.colors)
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece_id, db, who.identity)


@router.post("/pieces/{piece_id}/{action}", response_model=AdminPieceDetail, dependencies=[Depends(require_permission(perms.PUBLISH))])
def transition_piece(piece_id: uuid.UUID, action: content.TransitionAction, body: TransitionBody,
                     expected: datetime = Depends(expected_version), who: Actor = Depends(actor),
                     db: Session = Depends(get_db)) -> AdminPieceDetail:
    try:
        content.transition_piece(db, who, piece_id, expected, action.value, body.reason)
    except ContentError as exc:
        raise _fail(exc) from None
    return reads.get_piece(piece_id, db, who.identity)
