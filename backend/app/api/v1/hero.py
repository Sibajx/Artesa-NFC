"""P-028: the campaign the home hero shows today (public, read-only).

The site asks once per visit. ``data`` is null when no campaign applies, and
the site keeps its built-in hero. Cacheable for a few minutes: a change in
Gestión shows within that time.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services import hero

router = APIRouter(prefix="/hero", tags=["hero"])


class HeroVideo(BaseModel):
    mp4: str
    webm: str | None


class HeroCampaignPublic(BaseModel):
    slug: str
    name: str
    reason: str | None
    video: HeroVideo
    poster: str


class HeroEnvelope(BaseModel):
    data: HeroCampaignPublic | None


@router.get("", response_model=HeroEnvelope)
def current_hero(response: Response, db: Session = Depends(get_db)) -> HeroEnvelope:
    response.headers["Cache-Control"] = "public, max-age=300"
    campaign, reason = hero.current(db)
    return HeroEnvelope(data=hero.public_view(campaign, reason) if campaign else None)
