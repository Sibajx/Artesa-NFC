from __future__ import annotations

from pydantic import BaseModel

from app.schemas.media import MediaAssetPublic


class Location(BaseModel):
    locality: str | None
    municipality: str | None
    state: str | None
    country: str | None


class ArtisanSummary(BaseModel):
    slug: str
    full_name: str
    artistic_name: str | None


class ArtisanPieceSummary(BaseModel):
    slug: str
    name: str
    public_code: str
    availability_status: str
    cover_media: MediaAssetPublic | None


class ArtisanPublic(BaseModel):
    slug: str
    full_name: str
    artistic_name: str | None
    biography: str | None
    history: str | None
    location: Location
    techniques: list[str]
    languages: list[str]
    public_contact: dict | None
    media: list[MediaAssetPublic]
    pieces: list[ArtisanPieceSummary]
