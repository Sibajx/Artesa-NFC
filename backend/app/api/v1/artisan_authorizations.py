"""P-026 G3: the artisan's authorization link (public, token-gated).

Same rules as the design review link: the token travels in the request body
(the web page keeps it in the URL fragment), and every unusable token gets
the same answer.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services import authorizations

router = APIRouter(prefix="/artisan-authorizations", tags=["artisan-authorizations"])


class TokenBody(BaseModel):
    token: str = Field(max_length=100)


class DecisionBody(TokenBody):
    decision: Literal["authorize", "decline"]
    comment: str | None = Field(default=None, max_length=500)


class AuthorizationOpen(BaseModel):
    status: Literal["open"] = "open"
    full_name: str
    artistic_name: str | None
    place: str
    biography: str
    portrait: str | None
    expires_at: datetime


class AuthorizationUnavailable(BaseModel):
    status: Literal["unavailable"] = "unavailable"


class DecisionResult(BaseModel):
    status: Literal["recorded", "unavailable"]


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
    snap = row.snapshot
    return AuthorizationOpen(full_name=snap.get("full_name") or "", artistic_name=snap.get("artistic_name"),
                             place=snap.get("place") or "", biography=snap.get("biography") or "",
                             portrait=snap.get("portrait"), expires_at=row.expires_at)


@router.post("/decision", response_model=DecisionResult)
def decision(body: DecisionBody, request: Request, db: Session = Depends(get_db)) -> DecisionResult:
    recorded = authorizations.decide(db, body.token, authorize=body.decision == "authorize",
                                     comment=body.comment, ip=_ip(request))
    return DecisionResult(status="recorded" if recorded else "unavailable")
