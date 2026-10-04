"""Gestión Papelera (2026-10, PO decision): trash, restore and purge artisans
and pieces.

- **Trash** hides a draft or archived record from the normal Gestión lists
  (published ones must go back to draft first, so nothing public disappears
  by surprise). An artisan goes to the trash only when none of its pieces is
  outside it. While trashed, every content write and media upload is refused
  (services/content.py ``_locked``).
- **Restore** brings it back exactly as it was (its publication state never
  changed). A piece cannot come back while its artisan is in the trash.
- **Purge** deletes for good, and only what can never have been public, the
  same rule the PO chose for media: the record was never published since it
  was created (audited transitions, services/media.py ``ever_published``),
  has no certificate and no NFC tag (pieces), and has no pieces at all
  (artisans). Its media go too (rows, published files, originals; each
  leaves a tombstone so its number is never reused). The audit events stay:
  ``<kind>.deleted`` records what was removed.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.artisan import Artisan
from app.models.certificate import Certificate
from app.models.certificate_design import CertificateDesign
from app.models.sale import Sale
from app.models.enums import PublicationStatus
from app.models.media_asset import MediaAsset
from app.models.nfc_tag import NfcTag
from app.models.piece import Piece
from app.services import media
from app.services.content import Actor, ContentConflict, _audit, _finish, _locked, _touch

_MODEL = {"artisan": Artisan, "piece": Piece}


def _count(db: Session, stmt) -> int:
    return db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()


def _owned_media(db: Session, kind: str, row: Any) -> list[MediaAsset]:
    column = MediaAsset.artisan_id if kind == "artisan" else MediaAsset.piece_id
    return list(db.execute(select(MediaAsset).where(column == row.id)).scalars())


def purge_blocker(db: Session, kind: str, row: Any) -> str | None:
    """Why ``row`` cannot be purged (a stable code), or None when it can."""
    if row.trashed_at is None:
        return "not_trashed"
    if media.ever_published(db, kind, row, row.created_at):
        return "may_have_been_public"
    if kind == "piece":
        if _count(db, select(Certificate.id).where(Certificate.piece_id == row.id)):
            return "has_certificate"
        if _count(db, select(NfcTag.id).where(NfcTag.piece_id == row.id)):
            return "has_nfc_tag"
        if _count(db, select(Sale.id).where(Sale.piece_id == row.id)):
            return "has_sale"
    elif _count(db, select(Piece.id).where(Piece.artisan_id == row.id)):
        return "has_pieces"
    return None


_BLOCKER_TEXT = {
    "not_trashed": "Move it to the trash first.",
    "may_have_been_public": "It may have been public: it can stay archived or in the trash, but not be deleted.",
    "has_certificate": "The piece has a certificate (Certificación); it cannot be deleted.",
    "has_nfc_tag": "The piece has an NFC tag (Certificación); it cannot be deleted.",
    "has_pieces": "Delete or move this artisan's pieces first.",
    "has_sale": "The piece has a recorded sale (a financial record); it cannot be deleted.",
}


def trash(db: Session, actor: Actor, kind: str, entity_id: uuid.UUID, expected: datetime) -> Any:
    row = _locked(db, _MODEL[kind], entity_id, expected, kind, allow_trashed=True)
    if row.trashed_at is not None:
        raise ContentConflict("invalid_transition", f"The {kind} is already in the trash.")
    if row.publication_status == PublicationStatus.published:
        raise ContentConflict("published", "Move it back to draft before sending it to the trash.")
    if kind == "artisan" and _count(db, select(Piece.id).where(Piece.artisan_id == row.id, Piece.trashed_at.is_(None))):
        raise ContentConflict("has_pieces", "Send this artisan's pieces to the trash first.")
    row.trashed_at = datetime.now(timezone.utc)
    _touch(db, row)
    _audit(db, actor, kind, row.id, f"{kind}.trashed", {"publication_status": row.publication_status.value})
    _finish(db, row)
    return row


def untrash(db: Session, actor: Actor, kind: str, entity_id: uuid.UUID, expected: datetime) -> Any:
    row = _locked(db, _MODEL[kind], entity_id, expected, kind, allow_trashed=True)
    if row.trashed_at is None:
        raise ContentConflict("invalid_transition", f"The {kind} is not in the trash.")
    if kind == "piece" and db.get(Artisan, row.artisan_id).trashed_at is not None:
        raise ContentConflict("trashed_artisan", "Restore the piece's artisan from the trash first.")
    row.trashed_at = None
    _touch(db, row)
    _audit(db, actor, kind, row.id, f"{kind}.untrashed", {"publication_status": row.publication_status.value})
    _finish(db, row)
    return row


@dataclass(frozen=True)
class _Files:
    published: Path
    original: Path | None


def purge(db: Session, actor: Actor, media_root: Path | None, kind: str, entity_id: uuid.UUID,
          expected: datetime) -> None:
    row = _locked(db, _MODEL[kind], entity_id, expected, kind, allow_trashed=True)
    blocker = purge_blocker(db, kind, row)
    if blocker:
        raise ContentConflict(blocker, _BLOCKER_TEXT[blocker])
    assets = _owned_media(db, kind, row)
    if assets and media_root is None:
        raise media.media_unavailable()
    files: list[_Files] = []
    for asset in assets:
        published = media_root / "publico" / asset.storage_path
        original = media.original_of(db, media_root, asset)
        files.append(_Files(published, original))
        _audit(db, actor, "media_asset", asset.id, "media.deleted", {
            "owner_type": kind,
            "owner_id": str(row.id),
            "role": asset.role.value,
            "media_type": asset.media_type.value,
            "storage_path": asset.storage_path,
            "original_removed": original is not None,
            "reason": f"{kind}.deleted",
        })
        db.delete(asset)
    designs_deleted = 0
    if kind == "piece":
        # Certificate designs of a never-public piece (ADR-030 phase 5) were
        # never seen by anyone: they go with it. Without this the foreign key
        # would make the purge fail.
        for design in db.execute(select(CertificateDesign).where(CertificateDesign.piece_id == row.id)).scalars():
            db.delete(design)
            designs_deleted += 1
    snapshot = {"slug": row.slug, "media_deleted": len(assets)}
    if designs_deleted:
        snapshot["designs_deleted"] = designs_deleted
    snapshot["name" if kind == "piece" else "full_name"] = row.name if kind == "piece" else row.full_name
    if kind == "piece":
        snapshot["public_code"] = row.public_code
        snapshot["artisan_id"] = str(row.artisan_id)
    _audit(db, actor, kind, row.id, f"{kind}.deleted", snapshot)
    db.flush()
    db.delete(row)
    db.commit()
    # Files after the commit (same rule as media uploads and deletes).
    for f in files:
        media.remove_files(media_root / "publico", f.published, f.original)
