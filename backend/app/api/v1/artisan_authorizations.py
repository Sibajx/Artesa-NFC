"""P-026 G3: the artisan's authorization link (public, token-gated).

Same rules as the design review link: the token travels in the request body
(the web page keeps it in the URL fragment), and every unusable token gets
the same answer.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.artisan_authorization import AuthorizationStatus
from app.services import authorizations

router = APIRouter(prefix="/artisan-authorizations", tags=["artisan-authorizations"])


class TokenBody(BaseModel):
    token: str = Field(max_length=100)


class DecisionBody(TokenBody):
    decision: Literal["authorize", "changes", "decline"]
    comment: str | None = Field(default=None, max_length=500)


class SnapshotPiece(BaseModel):
    name: str
    cover: str | None = None


class AuthorizationOpen(BaseModel):
    status: Literal["open"] = "open"
    full_name: str
    artistic_name: str | None
    place: str
    biography: str
    history: str = ""
    techniques: list[str] = []
    languages: list[str] = []
    public_contact: dict[str, str] = {}
    portrait: str | None
    pieces: list[SnapshotPiece] = []
    # An authorization given in person, now confirmed by WhatsApp.
    confirming: bool = False
    expires_at: datetime


class AuthorizationUnavailable(BaseModel):
    status: Literal["unavailable"] = "unavailable"


class DecisionResult(BaseModel):
    status: Literal["recorded", "unavailable", "comment_required"]


def _ip(request: Request) -> str | None:
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


@router.post("/resolve", response_model=AuthorizationOpen | AuthorizationUnavailable)
def resolve(body: TokenBody, db: Session = Depends(get_db)) -> AuthorizationOpen | AuthorizationUnavailable:
    row = authorizations.by_token(db, body.token)
    if row is None:
        return AuthorizationUnavailable()
    snap = row.snapshot  # links sent before 2026-10-05 carry only the first five keys
    return AuthorizationOpen(full_name=snap.get("full_name") or "", artistic_name=snap.get("artistic_name"),
                             place=snap.get("place") or "", biography=snap.get("biography") or "",
                             history=snap.get("history") or "", techniques=snap.get("techniques") or [],
                             languages=snap.get("languages") or [],
                             public_contact={str(k): str(v) for k, v in (snap.get("public_contact") or {}).items()},
                             portrait=snap.get("portrait"), pieces=snap.get("pieces") or [],
                             confirming=row.status == AuthorizationStatus.authorized, expires_at=row.expires_at)


@router.post("/decision", response_model=DecisionResult)
def decision(body: DecisionBody, request: Request, background: BackgroundTasks,
             db: Session = Depends(get_db)) -> DecisionResult:
    # The owner's email goes out after the response (the relay can take seconds).
    return DecisionResult(status=authorizations.decide(
        db, body.token, decision=body.decision, comment=body.comment, ip=_ip(request),
        on_recorded=lambda *notice: background.add_task(authorizations.notify_operators, *notice)))
