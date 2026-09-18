from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.artisan import ArtisanPublic
from app.schemas.piece import PiecePublic


class CertificateResolveRequest(BaseModel):
    """POST /api/v1/certificates/resolve request body (API_CONTRACT.md
    section 7). `max_length` is a generous hard cap on work done before any
    plausibility/hash check (SECURITY.md section 4.1) - far larger than a
    real 43-character token, so it never doubles as a disguised
    length-based validity check; only a pathologically oversized payload is
    rejected here, through the project's standard 422 validation-error
    path."""

    token: str = Field(max_length=256)


class AuthenticityAuthentic(BaseModel):
    status: Literal["authentic"] = "authentic"
    certificate_version: int
    issued_at: datetime


class AuthenticityUnavailable(BaseModel):
    status: Literal["unavailable"] = "unavailable"


class AuthenticityMetadataPublic(BaseModel):
    notes: str | None


class CertificateResolveAuthentic(BaseModel):
    authenticity: AuthenticityAuthentic
    piece: PiecePublic
    artisan: ArtisanPublic
    authenticity_metadata: AuthenticityMetadataPublic


class CertificateResolveUnavailable(BaseModel):
    """API_CONTRACT.md section 7 / SECURITY.md section 4: the single,
    minimal public shape for every unresolvable token - unknown, tampered,
    revoked, draft, or resolved to a piece/artisan that is not public. No
    other field is ever added to this model; that uniformity is the entire
    point of it existing as its own fixed-shape class."""

    authenticity: AuthenticityUnavailable
