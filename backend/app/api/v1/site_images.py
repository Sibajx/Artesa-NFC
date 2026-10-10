"""P-029: the replaceable images of the public site (public, read-only).

``data`` maps a slot to its files; a slot without an entry keeps the image
built into the site. Cacheable for a few minutes, like the hero.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services import site_images

router = APIRouter(prefix="/site-images", tags=["site-images"])


class SiteImagePublic(BaseModel):
    avif: str
    webp: str
    jpg: str
    width: int
    height: int


class SiteImagesEnvelope(BaseModel):
    data: dict[str, SiteImagePublic]


@router.get("", response_model=SiteImagesEnvelope)
def current_site_images(response: Response, db: Session = Depends(get_db)) -> SiteImagesEnvelope:
    response.headers["Cache-Control"] = "public, max-age=300"
    return SiteImagesEnvelope(data={slot: SiteImagePublic(**site_images.public_view(row))
                                    for slot, row in site_images.all_images(db).items()})
