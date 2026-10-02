"""Response shapes of the Gestión admin API (ADR-029, API_CONTRACT.md section 14).

They expose internal ids and non-public records, but never:
- ``certificate.token_hash`` or a private token (API_CONTRACT.md section 11:
  forbidden on every endpoint, public or admin);
- ``nfc_tag.physical_uid``;
- ``media_asset.storage_path``.

Certificates and NFC tags are read-only here (ADR-026: issuing and revoking
stay in the provisioning CLI).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.media import MediaAssetPublic


class AdminMe(BaseModel):
    email: str


class AdminArtisanSummary(BaseModel):
    id: uuid.UUID
    slug: str
    full_name: str
    artistic_name: str | None
    publication_status: str
    piece_count: int
    updated_at: datetime


class AdminPieceSummary(BaseModel):
    id: uuid.UUID
    slug: str
    public_code: str
    name: str
    artisan_id: uuid.UUID
    artisan_slug: str
    publication_status: str
    availability_status: str
    updated_at: datetime


class AdminMedia(BaseModel):
    id: uuid.UUID
    status: str
    # The version to send back as If-Match (phase 4 media edits).
    updated_at: datetime
    media: MediaAssetPublic
    # True only for media that can never have been public (they may be
    # deleted for good); everything else can only be archived.
    deletable: bool = False


class AdminArtisanDetail(BaseModel):
    id: uuid.UUID
    slug: str
    full_name: str
    artistic_name: str | None
    locality: str | None
    municipality: str | None
    state: str | None
    country: str | None
    languages: list | None
    languages_public: bool
    biography: str | None
    history: str | None
    techniques: list | None
    public_contact: dict | None
    publication_status: str
    created_at: datetime
    updated_at: datetime
    media: list[AdminMedia]
    pieces: list[AdminPieceSummary]


class AdminArtisanRef(BaseModel):
    id: uuid.UUID
    slug: str
    full_name: str
    publication_status: str


class AdminCertificate(BaseModel):
    id: uuid.UUID
    status: str
    version: int
    issued_at: datetime | None
    revoked_at: datetime | None
    revocation_reason: str | None
    created_at: datetime


class AdminNfcTag(BaseModel):
    id: uuid.UUID
    status: str
    chip_model: str
    programmed_at: datetime | None
    locked_at: datetime | None
    created_at: datetime


class AdminPieceDetail(BaseModel):
    id: uuid.UUID
    slug: str
    public_code: str
    name: str
    description: str | None
    history: str | None
    technique: str | None
    materials: list | None
    origin: str | None
    creation_year: int | None
    creation_date: date | None
    dimensions: dict | None
    visual_theme: dict | None
    availability_status: str
    publication_status: str
    # True only when the piece and its artisan are both published
    # (API_CONTRACT.md section 9): what the public site would show.
    publicly_visible: bool
    created_at: datetime
    updated_at: datetime
    artisan: AdminArtisanRef
    media: list[AdminMedia]
    # Newest first; at most one is active (DATA_MODEL.md restriction C').
    certificates: list[AdminCertificate]
    # Newest first; at most one is programmed/locked (restriction B).
    nfc_tags: list[AdminNfcTag]


class AdminAuditEvent(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_type: str
    actor_email: str | None
    entity_type: str
    entity_id: uuid.UUID
    action: str
    result: str
    metadata: dict | None
