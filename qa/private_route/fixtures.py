"""Disposable test data, created through the real certificate lifecycle.

Runs in-process (the only place raw tokens ever exist). Refuses to touch
anything but an explicit test database on a loopback host: APP_ENV=test, a
test-marked database name (the same guard the pytest suite and the seed use)
and a 127.0.0.1/localhost server.

Data:
  * the repo's deterministic, fictional demo seed (app.db.seed.seed) - the
    static frontend pages exist for exactly these slugs;
  * VALID       active certificate on `mascara-demo-01` (notes + an internal
                metadata key that must never reach any response);
  * REVOKED     certificate on `vasija-demo-01`, activated then revoked;
  * UNPUBLISHED active certificate on a draft piece of a published artisan;
  * INVALID     a well-formed random token that was never issued.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from private_route.common import SECRETS, Report

VALID_PIECE_SLUG = "mascara-demo-01"
REVOKED_PIECE_SLUG = "vasija-demo-01"
UNPUBLISHED_PIECE_SLUG = "qa-unpublished-piece"
NOTES = "Nota QA de certificación"
LOOPBACK_DB_HOSTS = {"127.0.0.1", "localhost"}


@dataclass
class Fixtures:
    tokens: dict[str, str] = field(repr=False)  # alias -> raw token (memory only)
    hashes: dict[str, str] = field(repr=False)  # alias -> SHA-256 hex of the token
    certificate_ids: dict[str, str]  # alias -> certificate UUID (not secret)
    canary: str  # internal metadata value that must never be exposed
    piece_slug: str
    piece_name: str
    artisan_slug: str
    artisan_display_name: str
    artisan_location: str
    notes: str = NOTES

    def all_secret_strings(self) -> list[tuple[str, str]]:
        """(label, value) for every value that must not leak anywhere public."""
        items = [(f"token:{a}", v) for a, v in self.tokens.items()]
        items += [(f"token_hash:{a}", v) for a, v in self.hashes.items()]
        items += [(f"certificate_id:{a}", v) for a, v in self.certificate_ids.items()]
        items.append(("internal-metadata-canary", self.canary))
        return items


def build() -> Fixtures:
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.db_safety import assert_safe_for_tests, parse_database_target
    from app.db.base import SessionLocal
    from app.db.seed import seed
    from app.models.artisan import Artisan
    from app.models.certificate import Certificate, CertificateStatus
    from app.models.enums import PublicationStatus
    from app.models.piece import Piece
    from app.services.certificates import (
        activate_certificate,
        generate_certificate_token,
        hash_certificate_token,
        revoke_certificate,
    )

    settings = get_settings()
    assert_safe_for_tests(settings.app_env, settings.database_url)
    hosts = set(parse_database_target(settings.database_url).hosts)
    if not hosts or not hosts <= LOOPBACK_DB_HOSTS:
        raise RuntimeError("Refusing to seed QA data: the database host must be 127.0.0.1 or localhost.")

    canary = "QA-INTERNAL-" + secrets.token_hex(8)
    tokens: dict[str, str] = {}
    cert_ids: dict[str, str] = {}

    def piece_by_slug(db, slug: str) -> Piece:
        return db.execute(select(Piece).where(Piece.slug == slug)).scalar_one()

    def issue(db, piece: Piece, alias: str, metadata: dict | None = None) -> Certificate:
        certificate = Certificate(
            piece_id=piece.id, status=CertificateStatus.draft, authenticity_metadata=metadata
        )
        db.add(certificate)
        db.flush()
        result = activate_certificate(db, certificate)
        tokens[alias] = result.raw_token
        cert_ids[alias] = str(result.certificate.id)
        return result.certificate

    with SessionLocal() as db, db.begin():
        seed(db)
        valid_piece = piece_by_slug(db, VALID_PIECE_SLUG)
        revoked_piece = piece_by_slug(db, REVOKED_PIECE_SLUG)
        artisan = db.get(Artisan, valid_piece.artisan_id)

        unpublished_piece = Piece(
            slug=UNPUBLISHED_PIECE_SLUG,
            public_code="QA-UNPUBLISHED-01",
            artisan_id=artisan.id,
            name="Pieza QA sin publicar",
            publication_status=PublicationStatus.draft,
        )
        db.add(unpublished_piece)
        db.flush()

        issue(db, valid_piece, "valid", {"notes": NOTES, "internal": canary})
        revoked_certificate = issue(db, revoked_piece, "revoked")
        revoke_certificate(db, revoked_certificate)
        issue(db, unpublished_piece, "unpublished")

        facts = dict(
            piece_slug=valid_piece.slug,
            piece_name=valid_piece.name,
            artisan_slug=artisan.slug,
            artisan_display_name=artisan.artistic_name or artisan.full_name,
            artisan_location=", ".join(x for x in (artisan.locality, artisan.state) if x),
        )

    tokens["invalid"] = generate_certificate_token()
    hashes = {alias: hash_certificate_token(token) for alias, token in tokens.items()}
    for alias, token in tokens.items():
        SECRETS.add("TOKEN", alias, token)
        SECRETS.add("HASH", alias, hashes[alias])
    return Fixtures(
        tokens=tokens, hashes=hashes, certificate_ids=cert_ids, canary=canary, **facts
    )


def check_token_persistence(report: Report, fx: Fixtures) -> None:
    """Positive control: the DB holds token_hash, never the raw token. The
    certificate rows are pulled to this process and searched locally, so no
    secret is ever sent to the database as a query parameter."""
    from sqlalchemy import text

    from app.db.base import engine

    report.section("Token persistence (database)")
    with engine.connect() as connection:
        rows = [row[0] for row in connection.execute(text("SELECT c::text FROM certificate c"))]
    blob = "\n".join(rows)

    report.check(len(rows) == 3, "certificate table holds exactly the 3 issued certificates", f"rows={len(rows)}")
    problems = [
        f"raw token '{alias}' is stored in the database" for alias, token in fx.tokens.items() if token in blob
    ]
    report.group("no raw token appears in any certificate column (valid, revoked, unpublished, invalid)", problems)

    missing = [a for a in ("valid", "revoked", "unpublished") if blob.count(fx.hashes[a]) != 1]
    report.group("positive control: each issued certificate stores exactly its expected token_hash", [f"hash '{a}' not stored exactly once" for a in missing])
    report.check(fx.hashes["invalid"] not in blob, "the never-issued token has no stored hash")
