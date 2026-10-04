"""ADR-030 phase 5: the artisan's review link (public, token-gated).

The token travels in the request body, never in a path or query (the web
page keeps it in the URL fragment), so it does not reach access logs. Every
unusable token (unknown, expired, already decided) gets the same answer.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services import designs

router = APIRouter(prefix="/design-reviews", tags=["design-reviews"])


class TokenBody(BaseModel):
    token: str = Field(max_length=100)


class DecisionBody(TokenBody):
    decision: Literal["approve", "changes"]
    comment: str | None = Field(default=None, max_length=500)


class ReviewOpen(BaseModel):
    status: Literal["open"] = "open"
    piece_name: str
    artisan_name: str
    version: int
    expires_at: datetime
    svg: str


class ReviewUnavailable(BaseModel):
    status: Literal["unavailable"] = "unavailable"


class DecisionResult(BaseModel):
    status: Literal["recorded", "unavailable"]


def _ip(request: Request) -> str | None:
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


@router.post("/resolve", response_model=ReviewOpen | ReviewUnavailable)
def resolve_review(body: TokenBody, db: Session = Depends(get_db)) -> ReviewOpen | ReviewUnavailable:
    design = designs.by_review_token(db, body.token)
    if design is None:
        return ReviewUnavailable()
    return ReviewOpen(piece_name=design.params.get("piece_name") or "", artisan_name=design.params.get("artisan_name") or "",
                      version=design.version, expires_at=design.review_expires_at, svg=designs.svg(design))


@router.post("/decision", response_model=DecisionResult)
def decide(body: DecisionBody, request: Request, db: Session = Depends(get_db)) -> DecisionResult:
    recorded = designs.review_decision(db, body.token, approve=body.decision == "approve",
                                       comment=body.comment, ip=_ip(request))
    return DecisionResult(status="recorded" if recorded else "unavailable")
