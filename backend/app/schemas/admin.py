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
    # ADR-030: "editor" always; "designer" and/or "custodian" when granted.
    roles: list[str] = ["editor"]


class CustodyPiece(BaseModel):
    """A piece as seen from the custody area (ADR-030, phase 1)."""
    id: uuid.UUID
    slug: str
    public_code: str
    name: str
    artisan_name: str
    publication_status: str
    certificate_status: str | None
    certificate_version: int | None
    tag_status: str | None
    tag_chip: str | None
    ready_to_certify: bool
    # P-026 G6: what else the piece has, at a glance.
    card_status: str | None = None
    claimed: bool = False
    design_status: str | None = None
    sold: bool = False
    reported_stolen: bool = False


class AdminArtisanSummary(BaseModel):
    id: uuid.UUID
    slug: str
    full_name: str
    artistic_name: str | None
    publication_status: str
    piece_count: int
    updated_at: datetime
    trashed_at: datetime | None = None


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
    trashed_at: datetime | None = None


class AdminMedia(BaseModel):
    id: uuid.UUID
    status: str
    # The version to send back as If-Match (phase 4 media edits).
    updated_at: datetime
    media: MediaAssetPublic
    # True only for media that can never have been public (they may be
    # deleted for good); everything else can only be archived.
    deletable: bool = False


class AdminAuthorization(BaseModel):
    """P-026 G3: the artisan's current authorization request or grant."""
    id: uuid.UUID
    status: str
    medium: str
    requested_by: str
    created_at: datetime
    expires_at: datetime | None
    decided_at: datetime | None
    note: str | None


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
    # P-026 G3: private validation contact and the authorization to publish.
    validation_whatsapp: str | None = None
    validation_contact_name: str | None = None
    authorization: AdminAuthorization | None = None
    # The artisan's latest "Quiero cambios" / "No autorizo", until a new link
    # replaces it; its comment is in ``note``.
    last_answer: AdminAuthorization | None = None
    publication_status: str
    created_at: datetime
    updated_at: datetime
    media: list[AdminMedia]
    pieces: list[AdminPieceSummary]
    # Papelera: when set, the record is in the trash. `purge_blocker` is None
    # when it can be deleted for good, else the reason code (services/trash.py).
    trashed_at: datetime | None = None
    purge_blocker: str | None = None


class AdminArtisanRef(BaseModel):
    id: uuid.UUID
    slug: str
    full_name: str
    publication_status: str
    trashed_at: datetime | None = None


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


class AdminSale(BaseModel):
    """P-026 G1. The buyer's name and contact are personal data: Gestión only."""
    id: uuid.UUID
    status: str
    sold_on: date
    price_cents: int
    currency: str
    channel: str
    sold_by: str
    buyer_name: str | None
    buyer_contact: str | None
    note: str | None
    recorded_by: str
    created_at: datetime
    cancelled_at: datetime | None
    cancel_reason: str | None
    cancelled_by: str | None


class AdminPieceLocation(BaseModel):
    """P-026 G12. One move; the newest is where the piece is now."""
    id: uuid.UUID
    location: str
    place: str | None
    moved_on: date
    note: str | None
    recorded_by: str
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
    # P-026 G2: list price (cents), Gestión only.
    price_cents: int | None = None
    price_currency: str = "MXN"
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
    # Papelera: when set, the record is in the trash. `purge_blocker` is None
    # when it can be deleted for good, else the reason code (services/trash.py).
    trashed_at: datetime | None = None
    purge_blocker: str | None = None
    # False when the caller is not a custodian: certificates and nfc_tags are
    # then empty on purpose, not because the piece has none (ADR-030).
    custody_visible: bool = False
    # P-026 G1: every sale of the piece, newest first; at most one active.
    sales: list[AdminSale] = []
    locations: list[AdminPieceLocation] = []


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
