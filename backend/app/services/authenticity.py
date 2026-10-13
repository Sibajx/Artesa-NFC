"""The public face of ``certificate.authenticity_metadata`` (API_CONTRACT.md
section 7, PEND-030).

The column is free JSONB and stays internal; only the keys below ever leave the
API, each checked for its type on the way out. Gestión writes them through
``set_authenticity`` (custodians only), which keeps any other key already
stored untouched.

- ``verified_by``: who verified the piece and how, one short line
  ("Verificada en persona en el taller por Sol Cruz y Alexis").
- ``verified_on``: the day of that verification (ISO date, never in the future).
- ``edition``: "Pieza única" or "3 de 10".
- ``notes``: a short curated note about the certification.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.certificate import Certificate

LIMITS = {"verified_by": 160, "edition": 60, "notes": 280}
PUBLIC_KEYS = ("verified_by", "verified_on", "edition", "notes")


class InvalidAuthenticity(ValueError):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class AuthenticityPublic:
    verified_by: str | None
    verified_on: date | None
    edition: str | None
    notes: str | None


def _text(value: object, key: str) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[: LIMITS[key]] if value else None


def _day(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def project(metadata: dict | None) -> AuthenticityPublic:
    """The allowlist projection: only these keys, each of the right type."""
    metadata = metadata if isinstance(metadata, dict) else {}
    return AuthenticityPublic(
        verified_by=_text(metadata.get("verified_by"), "verified_by"),
        verified_on=_day(metadata.get("verified_on")),
        edition=_text(metadata.get("edition"), "edition"),
        notes=_text(metadata.get("notes"), "notes"),
    )


def set_authenticity(
    db: Session,
    certificate: Certificate,
    *,
    verified_by: str | None,
    verified_on: date | None,
    edition: str | None,
    notes: str | None,
) -> dict:
    """Replace the four public keys (empty or None removes one) and keep every
    other key of the column as it was. Returns what was stored."""
    values: dict[str, str | None] = {}
    for key, raw in (("verified_by", verified_by), ("edition", edition), ("notes", notes)):
        text = (raw or "").strip()
        if len(text) > LIMITS[key]:
            raise InvalidAuthenticity(key, "too_long")
        values[key] = text or None
    if verified_on is not None and verified_on > datetime.now(timezone.utc).date():
        raise InvalidAuthenticity("verified_on", "in_the_future")
    values["verified_on"] = verified_on.isoformat() if verified_on else None

    metadata = dict(certificate.authenticity_metadata or {})
    for key in PUBLIC_KEYS:
        if values[key] is None:
            metadata.pop(key, None)
        else:
            metadata[key] = values[key]
    certificate.authenticity_metadata = metadata or None
    flag_modified(certificate, "authenticity_metadata")
    db.flush()
    return {k: values[k] for k in PUBLIC_KEYS}
