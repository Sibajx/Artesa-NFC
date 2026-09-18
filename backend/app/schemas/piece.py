from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.schemas.artisan import ArtisanSummary
from app.schemas.media import MediaAssetPublic


class Dimensions(BaseModel):
    height: float | None = None
    width: float | None = None
    depth: float | None = None
    unit: str | None = None


class PiecePublic(BaseModel):
    slug: str
    public_code: str
    name: str
    description: str | None
    history: str | None
    materials: list[str]
    technique: str | None
    origin: str | None
    creation_year: int | None
    creation_date: date | None
    dimensions: Dimensions | None
    visual_theme: dict | None
    availability_status: str
    artisan: ArtisanSummary
    media: list[MediaAssetPublic]
