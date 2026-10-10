"""ADR-030 phase 5: Gestión's certificate design area (designer role).

Designers (DESIGNER_EMAILS, and every custodian) create versions, edit
drafts with a live preview, send them for the artisan's review, record an
approval and publish. Editors get 403.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.admin.writes import _fail, actor, expected_version, require_write_guard
from app.api.deps import get_db
from app.core.access import DESIGNER, FORBIDDEN_ERROR, AdminIdentity, require_admin
from app.core.config import get_settings
from app.models.certificate_design import CertificateDesign
from app.services import designs
from app.services.content import Actor, ContentError


def require_designer(identity: AdminIdentity = Depends(require_admin)) -> AdminIdentity:
    if not identity.has(DESIGNER):
        raise HTTPException(status_code=403, detail=FORBIDDEN_ERROR)
    return identity


class DesignSummary(BaseModel):
    id: uuid.UUID
    piece_id: uuid.UUID
    version: int
    status: str
    template: str
    params: dict[str, Any]
    created_by: str
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None
    review_expires_at: datetime | None
    change_request: str | None
    approved_at: datetime | None
    approved_by_name: str | None
    approval_medium: str | None
    approval_note: str | None
    approval_recorded_by: str | None
    published_at: datetime | None
    published_by: str | None


class DesignDetail(DesignSummary):
    svg: str


class DesignList(BaseModel):
    data: list[DesignSummary]


class Submitted(DesignDetail):
    """The only response that carries the review link (shown once)."""
    review_url: str


class ParamsBody(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class PreviewBody(ParamsBody):
    version: int = Field(default=1, ge=1, le=999)


class ApprovalBody(BaseModel):
    name: str = Field(max_length=120)
    medium: str = Field(max_length=20)
    note: str = Field(max_length=500)


class Svg(BaseModel):
    svg: str


def _summary(d: CertificateDesign) -> dict[str, Any]:
    return {c: getattr(d, c) for c in DesignSummary.model_fields if c != "status"} | {"status": d.status.value}


def _detail(db: Session, d: CertificateDesign) -> DesignDetail:
    return DesignDetail(**_summary(d), svg=designs.svg(db, d))


reads = APIRouter(prefix="/api/admin/v1", tags=["admin", "designs"],
                  dependencies=[Depends(require_designer)])
writes = APIRouter(prefix="/api/admin/v1", tags=["admin", "designs"],
                   dependencies=[Depends(require_designer), Depends(require_write_guard)])


@reads.get("/pieces/{piece_id}/designs", response_model=DesignList)
def list_designs(piece_id: uuid.UUID, db: Session = Depends(get_db)) -> DesignList:
    return DesignList(data=[DesignSummary(**_summary(d)) for d in designs.versions(db, piece_id)])


@reads.get("/designs/{design_id}", response_model=DesignDetail)
def get_design(design_id: uuid.UUID, db: Session = Depends(get_db)) -> DesignDetail:
    design = db.get(CertificateDesign, design_id)
    if design is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "The requested resource does not exist."})
    return _detail(db, design)


@writes.post("/designs/preview", response_model=Svg)
def preview(body: PreviewBody, db: Session = Depends(get_db)) -> Svg:
    try:
        return Svg(svg=designs.preview(db, body.params, body.version))
    except ContentError as exc:
        raise _fail(exc) from None


def _run(call) -> CertificateDesign:
    try:
        return call()
    except ContentError as exc:
        raise _fail(exc) from None


@writes.post("/pieces/{piece_id}/designs", status_code=201, response_model=DesignDetail)
def create_design(piece_id: uuid.UUID, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> DesignDetail:
    return _detail(db, _run(lambda: designs.create(db, who, piece_id)))


@writes.patch("/designs/{design_id}", response_model=DesignDetail)
def update_design(design_id: uuid.UUID, body: ParamsBody, expected: datetime = Depends(expected_version),
                  who: Actor = Depends(actor), db: Session = Depends(get_db)) -> DesignDetail:
    return _detail(db, _run(lambda: designs.update(db, who, design_id, expected, body.params)))


@writes.post("/designs/{design_id}/submit", response_model=Submitted)
def submit_design(design_id: uuid.UUID, expected: datetime = Depends(expected_version),
                  who: Actor = Depends(actor), db: Session = Depends(get_db)) -> Submitted:
    try:
        design, token = designs.submit(db, who, design_id, expected)
    except ContentError as exc:
        raise _fail(exc) from None
    return Submitted(**_detail(db, design).model_dump(), review_url=designs.review_url(get_settings().app_env, token))


@writes.post("/designs/{design_id}/approve", response_model=DesignDetail)
def approve_design(design_id: uuid.UUID, body: ApprovalBody, expected: datetime = Depends(expected_version),
                   who: Actor = Depends(actor), db: Session = Depends(get_db)) -> DesignDetail:
    return _detail(db, _run(lambda: designs.record_approval(db, who, design_id, expected, name=body.name,
                                                        medium=body.medium, note=body.note)))


@writes.post("/designs/{design_id}/publish", response_model=DesignDetail)
def publish_design(design_id: uuid.UUID, expected: datetime = Depends(expected_version),
                   who: Actor = Depends(actor), db: Session = Depends(get_db)) -> DesignDetail:
    return _detail(db, _run(lambda: designs.publish(db, who, design_id, expected)))


@writes.post("/designs/{design_id}/discard", status_code=204)
def discard_design(design_id: uuid.UUID, expected: datetime = Depends(expected_version),
                   who: Actor = Depends(actor), db: Session = Depends(get_db)) -> None:
    try:
        designs.discard(db, who, design_id, expected)
    except ContentError as exc:
        raise _fail(exc) from None


# --- Phase 5b: the team's artwork -------------------------------------------------------
#
# The file itself is the body (no multipart), as for media uploads: a
# cross-site page cannot send X-Artesa-Admin with an image/* body without a
# CORS preflight, which the admin API never grants. The answer is the art's
# id; a draft uses it through params["art"] and the usual PATCH.

from fastapi import Request  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402

from app.api.admin.writes import ADMIN_WRITE_HEADER, CSRF_ERROR, _same_origin  # noqa: E402
from app.services import certificate_art  # noqa: E402

ART_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
ART_TYPE_ERROR = {"code": "unsupported_media_type", "message": "Send the artwork as the body: PNG, JPEG or WebP."}
ART_TOO_LARGE = {"code": "too_large", "message": "The artwork is larger than 8 MB."}


def require_art_upload_guard(request: Request) -> None:
    if request.headers.get(ADMIN_WRITE_HEADER) != "1" or not _same_origin(request):
        raise HTTPException(status_code=403, detail=CSRF_ERROR)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() not in ART_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail=ART_TYPE_ERROR)


class ArtUploaded(BaseModel):
    id: str
    mime_type: str
    width: int
    height: int


art_router = APIRouter(prefix="/api/admin/v1", tags=["admin", "designs"],
                       dependencies=[Depends(require_designer), Depends(require_art_upload_guard)])


@art_router.post("/certificate-art", status_code=201, response_model=ArtUploaded)
async def upload_art(request: Request, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> ArtUploaded:
    limit = certificate_art.MAX_UPLOAD_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isascii() and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=ART_TOO_LARGE)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=ART_TOO_LARGE)
    try:
        art = await run_in_threadpool(certificate_art.store, db, who.identity.email, bytes(data))
    except ContentError as exc:
        raise _fail(exc) from None
    return ArtUploaded(id=art.sha256, mime_type=art.mime_type, width=art.width, height=art.height)
