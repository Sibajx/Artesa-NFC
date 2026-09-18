from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.schemas.artisan import ArtisanSummary
from app.schemas.media import MediaAssetPublic

if TYPE_CHECKING:
    from app.models.piece import Piece


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


def piece_to_public(piece: "Piece", media: list[MediaAssetPublic]) -> PiecePublic:
    """Map an already visibility-checked Piece (API_CONTRACT.md section 9)
    to its public representation (section 5). Shared by `GET
    /pieces/{slug}` and the certificate resolve endpoint's `piece` field so
    both surfaces stay byte-for-byte consistent."""
    dimensions = Dimensions(**piece.dimensions) if piece.dimensions else None
    return PiecePublic(
        slug=piece.slug,
        public_code=piece.public_code,
        name=piece.name,
        description=piece.description,
        history=piece.history,
        materials=piece.materials or [],
        technique=piece.technique,
        origin=piece.origin,
        creation_year=piece.creation_year,
        creation_date=piece.creation_date,
        dimensions=dimensions,
        visual_theme=piece.visual_theme,
        availability_status=piece.availability_status.value,
        artisan=ArtisanSummary(
            slug=piece.artisan.slug,
            full_name=piece.artisan.full_name,
            artistic_name=piece.artisan.artistic_name,
        ),
        media=media,
    )
