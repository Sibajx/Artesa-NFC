"""Request bodies of the Gestión admin writes (phase 2, API_CONTRACT.md §14.2).

Unknown fields are rejected, not ignored, so a typo never silently drops a
change. Text is trimmed; an empty optional text becomes null.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.models.media_asset import MediaRole
from app.models.piece import AvailabilityStatus

Slug = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=60)]
PublicCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=10000)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


def _upper(value):
    return value.strip().upper() if isinstance(value, str) else value


def _blank_to_none(value):
    if isinstance(value, str) and not value.strip():
        return None
    return value


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtisanFields(_Body):
    artistic_name: ShortText | None = None
    locality: ShortText | None = None
    municipality: ShortText | None = None
    state: ShortText | None = None
    country: ShortText | None = None
    languages: list[Tag] | None = Field(default=None, max_length=20)
    languages_public: bool = False
    biography: LongText | None = None
    history: LongText | None = None
    techniques: list[Tag] | None = Field(default=None, max_length=20)
    public_contact: dict[Tag, ShortText] | None = Field(default=None, max_length=10)

    _blank = field_validator(
        "artistic_name", "locality", "municipality", "state", "country", "biography", "history", mode="before"
    )(_blank_to_none)


class ArtisanCreate(ArtisanFields):
    full_name: Name
    slug: Slug | None = None
    state: ShortText | None = "Oaxaca"
    country: ShortText | None = "México"


class ArtisanUpdate(ArtisanFields):
    full_name: Name | None = None
    slug: Slug | None = None
    languages_public: bool | None = None  # type: ignore[assignment]


class PieceFields(_Body):
    description: LongText | None = None
    history: LongText | None = None
    technique: ShortText | None = None
    materials: list[Tag] | None = Field(default=None, max_length=20)
    origin: ShortText | None = None
    creation_year: int | None = Field(default=None, ge=1000, le=2100)
    creation_date: date | None = None
    dimensions: dict[Tag, float | ShortText] | None = Field(default=None, max_length=10)
    # P-026 G2: list price in cents, Gestión only.
    price_cents: int | None = Field(default=None, ge=0, le=10_000_000_000)
    price_currency: Literal["MXN", "USD"] | None = None

    _blank = field_validator("description", "history", "technique", "origin", mode="before")(_blank_to_none)


class PieceCreate(PieceFields):
    artisan_id: uuid.UUID
    name: Name
    slug: Slug | None = None
    public_code: PublicCode | None = None
    availability_status: AvailabilityStatus = AvailabilityStatus.available

    _code = field_validator("public_code", mode="before")(_upper)


class PieceUpdate(PieceFields):
    artisan_id: uuid.UUID | None = None
    name: Name | None = None
    slug: Slug | None = None
    public_code: PublicCode | None = None

    _code = field_validator("public_code", mode="before")(_upper)


class TransitionBody(_Body):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None


class AvailabilityBody(_Body):
    availability_status: AvailabilityStatus


AltText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class MediaUpdate(_Body):
    alt_text: AltText | None = None
    position: int | None = Field(default=None, ge=0, le=999)
    # The service checks it against the owner's roles and the file's type.
    role: MediaRole | None = None

    _blank = field_validator("alt_text", mode="before")(_blank_to_none)


def provided(body: BaseModel, *, never_null: tuple[str, ...] = ()) -> dict:
    """Only the fields the client actually sent (PATCH semantics). Fields that
    may not be cleared are dropped when sent as null."""
    data = body.model_dump(include=body.model_fields_set)
    return {k: v for k, v in data.items() if not (k in never_null and v is None)}


# --- P-026 G1: sales -----------------------------------------------------------------


class SaleBody(_Body):
    sold_on: date
    price_cents: int = Field(ge=0, le=10_000_000_000)
    currency: Literal["MXN", "USD"] = "MXN"
    channel: Literal["taller", "tienda", "en_linea", "feria", "otro"]
    sold_by: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    buyer_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    buyer_contact: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None

    _blank = field_validator("buyer_name", "buyer_contact", "note", mode="before")(_blank_to_none)


class SaleCancelBody(_Body):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]
