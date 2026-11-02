"""Gestión → calculadora de precio al público.

Reading the settings and asking for a quote needs the Sales permission;
changing the settings (tax model, rates, tiers) is the owner's alone. Nothing
here is public."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.admin.accounts import require_owner
from app.api.admin.writes import actor, require_write_guard
from app.api.deps import get_db
from app.core import permissions as perms
from app.core.access import FORBIDDEN_ERROR, require_permission
from app.models.piece import Piece
from app.services import pricing
from app.services.content import Actor

reads = APIRouter(prefix="/api/admin/v1", tags=["admin", "pricing"],
                  dependencies=[Depends(require_permission(perms.SALES))])
writes = APIRouter(prefix="/api/admin/v1", tags=["admin", "pricing"],
                   dependencies=[Depends(require_owner), Depends(require_write_guard)])


class SettingsOut(BaseModel):
    config: pricing.PricingConfig
    updated_at: datetime | None
    updated_by: str | None


class QuoteBody(BaseModel):
    artisan_price_cents: int = Field(ge=0, le=pricing.MAX_CENTS)
    commission_pct: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    piece_id: uuid.UUID | None = None  # only to check it exists; the numbers come from the body


def _settings(db: Session) -> SettingsOut:
    config, at, by = pricing.load(db)
    return SettingsOut(config=config, updated_at=at, updated_by=by)


@reads.get("/pricing/settings", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db)) -> SettingsOut:
    return _settings(db)


@reads.post("/pricing/quote", response_model=pricing.Quote)
def get_quote(body: QuoteBody, db: Session = Depends(get_db)) -> pricing.Quote:
    if body.piece_id is not None and db.get(Piece, body.piece_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "La pieza no existe."})
    config, _, _ = pricing.load(db)
    return pricing.quote(config, body.artisan_price_cents, body.commission_pct)


@writes.put("/pricing/settings", response_model=SettingsOut)
def put_settings(body: pricing.PricingConfig, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> SettingsOut:
    if not who.identity.can(perms.SALES):  # the owner always can; kept explicit
        raise HTTPException(status_code=403, detail=FORBIDDEN_ERROR)
    pricing.save(db, who, body)
    return _settings(db)
