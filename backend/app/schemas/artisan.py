from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.schemas.media import MediaAssetPublic

if TYPE_CHECKING:
    from app.models.artisan import Artisan


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


def artisan_to_public(
    artisan: "Artisan", media: list[MediaAssetPublic], pieces: list[ArtisanPieceSummary]
) -> ArtisanPublic:
    """Map an already visibility-checked Artisan to its public
    representation (API_CONTRACT.md section 4). Shared by `GET
    /artisans/{slug}` and the certificate resolve endpoint's `artisan`
    field so both surfaces stay byte-for-byte consistent."""
    return ArtisanPublic(
        slug=artisan.slug,
        full_name=artisan.full_name,
        artistic_name=artisan.artistic_name,
        biography=artisan.biography,
        history=artisan.history,
        location=Location(
            locality=artisan.locality,
            municipality=artisan.municipality,
            state=artisan.state,
            country=artisan.country,
        ),
        techniques=artisan.techniques or [],
        languages=(artisan.languages or []) if artisan.languages_public else [],
        public_contact=artisan.public_contact,
        media=media,
        pieces=pieces,
    )
