"""The home's collection entries (public, read-only): the active ones with a
photo, in order. An empty list keeps the single entry built into the site.
Cacheable for a few seconds, like the hero and the site images."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services import collections

router = APIRouter(prefix="/collections", tags=["collections"])


class CollectionImage(BaseModel):
    avif: str
    webp: str
    jpg: str
    width: int
    height: int


class CollectionPublic(BaseModel):
    id: str
    title: str
    body: str | None
    cta_label: str
    link: str
    image: CollectionImage


class CollectionsEnvelope(BaseModel):
    data: list[CollectionPublic]


@router.get("", response_model=CollectionsEnvelope)
def current_collections(response: Response, db: Session = Depends(get_db)) -> CollectionsEnvelope:
    response.headers["Cache-Control"] = "public, max-age=30"
    return CollectionsEnvelope(data=[CollectionPublic(**collections.public_view(e)) for e in collections.public_entries(db)])
