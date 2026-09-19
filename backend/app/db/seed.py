"""Deterministic, idempotent demo seed fixtures.

Run with:

    python -m app.db.seed

Scope and safety contract (Sprint 3, fictional demo data only):

- Every fixture's identity is a deterministic UUID: ``uuid.uuid5(SEED_NAMESPACE,
  <stable fixture key>)``. ``uuid.uuid4()`` is never called here.
- The seed only ever creates or updates the exact approved demo fixtures
  below. If an approved ``slug``/``public_code`` is already taken by a row
  with a *different* id than the deterministic id this seed expects, that
  means a non-demo (or otherwise foreign) record occupies that identity, and
  the whole run aborts with ``SeedCollisionError`` and rolls back rather than
  silently adopting or overwriting it.
- Everything runs in a single transaction: any collision or database error
  rolls back the complete run.
- The CLI refuses to run unless APP_ENV is ``local`` or ``test`` and the
  database target is clearly non-production (app/core/db_safety.py); the
  check happens before any connection is opened.
"""
from __future__ import annotations

import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db_safety import UnsafeConfigurationError, assert_safe_for_seed
from app.db.base import SessionLocal
from app.models.artisan import Artisan
from app.models.enums import PublicationStatus
from app.models.media_asset import MediaAsset, MediaRole, MediaType
from app.models.piece import AvailabilityStatus, Piece

# Fixed and never regenerated: every deterministic id below is derived from
# this namespace plus a stable fixture key. Changing it would change every id.
SEED_NAMESPACE = uuid.UUID("d6f6a7d4-6c8b-4f1a-9d3e-2b6a7c8d9e0f")


class SeedCollisionError(RuntimeError):
    """An approved fixture's slug/public_code already exists under a
    different id than this seed expects. Raised instead of silently
    overwriting, renaming, or adopting the conflicting record."""


def _fixture_id(key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, key)


ARTISANS = [
    {
        "key": "artisan-demo-01",
        "slug": "artesano-demo-01",
        "full_name": "Artesano Demo Uno",
        "artistic_name": None,
        "locality": "San Ficticio de las Tallas",
        "municipality": "Municipio Demo de Cuilápam",
        "techniques": ["talla en madera"],
        "biography": "Artesano ficticio de demostración especializado en talla de madera.",
    },
    {
        "key": "artesana-demo-02",
        "slug": "artesana-demo-02",
        "full_name": "Artesana Demo Dos",
        "artistic_name": None,
        "locality": "Villa Ficticia del Telar",
        "municipality": "Municipio Demo del Telar",
        "techniques": ["telar de cintura"],
        "biography": "Artesana ficticia de demostración especializada en textil de telar.",
    },
    {
        "key": "artisan-demo-03",
        "slug": "artesano-demo-03",
        "full_name": "Artesano Demo Tres",
        "artistic_name": None,
        "locality": "Barrio Ficticio del Barro",
        "municipality": "Municipio Demo de Cerámica",
        "techniques": ["cerámica"],
        "biography": "Artesano ficticio de demostración especializado en cerámica.",
    },
]

PIECES = [
    {
        "key": "mascara-demo-01",
        "slug": "mascara-demo-01",
        "public_code": "DEMO-MASCARA-01",
        "artisan_key": "artisan-demo-01",
        "name": "Máscara Demo Uno",
        "description": "Pieza ficticia de demostración: máscara tallada en madera.",
        "technique": "Talla en madera",
    },
    {
        "key": "figura-tallada-demo-01",
        "slug": "figura-tallada-demo-01",
        "public_code": "DEMO-FIGURA-01",
        "artisan_key": "artisan-demo-01",
        "name": "Figura Tallada Demo Uno",
        "description": "Pieza ficticia de demostración: figura tallada en madera.",
        "technique": "Talla en madera",
    },
    {
        "key": "textil-demo-01",
        "slug": "textil-demo-01",
        "public_code": "DEMO-TEXTIL-01",
        "artisan_key": "artesana-demo-02",
        "name": "Textil Demo Uno",
        "description": "Pieza ficticia de demostración: textil elaborado en telar de cintura.",
        "technique": "Telar de cintura",
    },
    {
        "key": "vasija-demo-01",
        "slug": "vasija-demo-01",
        "public_code": "DEMO-VASIJA-01",
        "artisan_key": "artisan-demo-03",
        "name": "Vasija Demo Uno",
        "description": "Pieza ficticia de demostración: vasija de cerámica.",
        "technique": "Cerámica",
    },
]


def _upsert_by_id(
    session: Session,
    model: type,
    id_: uuid.UUID,
    unique_fields: list[str],
    attrs: dict,
):
    """Get-or-create ``model`` by deterministic ``id_``, refusing to touch a
    row that already owns one of ``unique_fields`` under a different id."""
    for field_name in unique_fields:
        value = attrs[field_name]
        other = session.execute(
            select(model).where(getattr(model, field_name) == value)
        ).scalar_one_or_none()
        if other is not None and other.id != id_:
            raise SeedCollisionError(
                f"{model.__name__}.{field_name}={value!r} already exists with "
                f"id={other.id}, but this demo seed expects deterministic "
                f"id={id_} for that value. Refusing to modify, rename, or "
                "adopt a record outside the approved demo fixture set."
            )

    existing = session.get(model, id_)
    if existing is None:
        session.add(model(id=id_, **attrs))
        return

    for field_name, value in attrs.items():
        if getattr(existing, field_name) != value:
            setattr(existing, field_name, value)


def seed(session: Session) -> dict:
    stats = {"artisans": 0, "pieces": 0, "media_assets": 0}
    artisan_ids: dict[str, uuid.UUID] = {}
    piece_ids: dict[str, uuid.UUID] = {}

    for fixture in ARTISANS:
        id_ = _fixture_id(fixture["key"])
        artisan_ids[fixture["key"]] = id_
        attrs = {
            "slug": fixture["slug"],
            "full_name": fixture["full_name"],
            "artistic_name": fixture["artistic_name"],
            "locality": fixture["locality"],
            "municipality": fixture["municipality"],
            "techniques": fixture["techniques"],
            "biography": fixture["biography"],
            "publication_status": PublicationStatus.published,
        }
        _upsert_by_id(session, Artisan, id_, ["slug"], attrs)
        stats["artisans"] += 1

    session.flush()

    for fixture in PIECES:
        id_ = _fixture_id(fixture["key"])
        piece_ids[fixture["key"]] = id_
        attrs = {
            "slug": fixture["slug"],
            "public_code": fixture["public_code"],
            "artisan_id": artisan_ids[fixture["artisan_key"]],
            "name": fixture["name"],
            "description": fixture["description"],
            "technique": fixture["technique"],
            "availability_status": AvailabilityStatus.available,
            "publication_status": PublicationStatus.published,
        }
        _upsert_by_id(session, Piece, id_, ["slug", "public_code"], attrs)
        stats["pieces"] += 1

    session.flush()

    for fixture in ARTISANS:
        id_ = _fixture_id(f"media:{fixture['key']}:portrait")
        attrs = {
            "artisan_id": artisan_ids[fixture["key"]],
            "piece_id": None,
            "media_type": MediaType.image,
            "role": MediaRole.portrait,
            "storage_path": f"demo/artisans/{fixture['key']}/portrait.jpg",
            "alt_text": f"Retrato ficticio de demostración de {fixture['full_name']}",
            "position": 0,
        }
        _upsert_by_id(session, MediaAsset, id_, [], attrs)
        stats["media_assets"] += 1

    for fixture in PIECES:
        id_ = _fixture_id(f"media:{fixture['key']}:hero")
        attrs = {
            "artisan_id": None,
            "piece_id": piece_ids[fixture["key"]],
            "media_type": MediaType.image,
            "role": MediaRole.hero,
            "storage_path": f"demo/pieces/{fixture['key']}/hero.jpg",
            "alt_text": f"Fotografía ficticia de demostración de {fixture['name']}",
            "position": 0,
        }
        _upsert_by_id(session, MediaAsset, id_, [], attrs)
        stats["media_assets"] += 1

    session.flush()
    return stats


def main() -> None:
    # Refuse before any session/connection exists: the seed must never write
    # to production or to an unclear target (see app/core/db_safety.py).
    settings = get_settings()
    try:
        assert_safe_for_seed(settings.app_env, settings.database_url)
    except UnsafeConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    with SessionLocal() as session:
        try:
            with session.begin():
                stats = seed(session)
        except Exception as exc:
            print(f"Seed failed, transaction rolled back: {exc}", file=sys.stderr)
            sys.exit(1)

    print(
        "Seed complete: "
        f"{stats['artisans']} artisans, {stats['pieces']} pieces, "
        f"{stats['media_assets']} media assets."
    )


if __name__ == "__main__":
    main()
