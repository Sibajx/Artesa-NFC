"""ADR-030 phase 4: the piece's palette for its generic (public) certificate.

3 to 5 colours taken from the piece's cover photo (the first active hero
photo, else its first active photo: the same cover the public lists use) and
stored in ``piece.visual_theme`` (ADR-012) as::

    {"palette": ["#5c3f28", "#c9761c", ...], "palette_source": "auto" | "manual"}

Other keys of ``visual_theme`` are kept. ``auto`` is filled the first time
a piece gets a photo; a person can regenerate it or set it by hand
(``manual``), which the automatic fill never overwrites.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.media_asset import MediaAsset, MediaAssetStatus, MediaRole, MediaType
from app.models.piece import Piece
from app.services.content import Actor, ContentConflict, _audit, _finish, _locked, _touch

MIN_COLORS, MAX_COLORS = 3, 5
HEX_RE = re.compile(r"#[0-9a-f]{6}")
# Two colours closer than this (Euclidean, 0-255 RGB) count as the same.
_MIN_DISTANCE = 40
_SAMPLE_SIZE = 160


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def extract(path: Path) -> list[str]:
    """The photo's main colours, most present first, distinct from each
    other. Raises ValueError for an unreadable image."""
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((_SAMPLE_SIZE, _SAMPLE_SIZE))
            quantized = image.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
            palette = quantized.getpalette() or []
            counts = sorted(quantized.getcolors() or [], reverse=True)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("unreadable image") from exc
    picked: list[tuple[int, int, int]] = []
    for _count, index in counts:
        rgb = tuple(palette[index * 3: index * 3 + 3])
        if len(rgb) == 3 and all(_distance(rgb, other) >= _MIN_DISTANCE for other in picked):
            picked.append(rgb)  # type: ignore[arg-type]
        if len(picked) == MAX_COLORS:
            break
    # A nearly flat photo: complete with lighter and darker shades of the main colour.
    base = picked[0] if picked else (128, 128, 128)
    shade = 0.45
    while len(picked) < MIN_COLORS:
        factor = 1 + shade if len(picked) % 2 else 1 - shade
        picked.append(tuple(max(0, min(255, round(c * factor))) for c in base))  # type: ignore[arg-type]
        shade /= 2
    return [_hex(rgb) for rgb in picked]


def cover_of(db: Session, piece_id: uuid.UUID) -> MediaAsset | None:
    photos = db.execute(
        select(MediaAsset).where(
            MediaAsset.piece_id == piece_id,
            MediaAsset.media_type == MediaType.image,
            MediaAsset.status == MediaAssetStatus.active,
        ).order_by(MediaAsset.position.asc(), MediaAsset.created_at.asc())
    ).scalars().all()
    return next((m for m in photos if m.role == MediaRole.hero), photos[0] if photos else None)


def _palette_from_cover(db: Session, media_root: Path | None, piece_id: uuid.UUID) -> list[str]:
    cover = cover_of(db, piece_id)
    if cover is None or media_root is None:
        raise ContentConflict("no_cover_photo", "The piece has no active photo to take colours from.")
    try:
        return extract(media_root / "publico" / cover.storage_path)
    except (ValueError, FileNotFoundError):
        raise ContentConflict("unreadable_cover_photo", "The cover photo could not be read.") from None


def _store(db: Session, actor: Actor, piece: Piece, palette: list[str], source: str, *, touch: bool = True) -> None:
    before = (piece.visual_theme or {}).get("palette")
    piece.visual_theme = {**(piece.visual_theme or {}), "palette": palette, "palette_source": source}
    if touch:
        _touch(db, piece)
    _audit(db, actor, "piece", piece.id, "piece.palette_set", {"from": before, "to": palette, "source": source})
    _finish(db, piece)


def generate(db: Session, actor: Actor, media_root: Path | None, piece_id: uuid.UUID, expected: datetime) -> Piece:
    piece = _locked(db, Piece, piece_id, expected, "piece")
    _store(db, actor, piece, _palette_from_cover(db, media_root, piece.id), "auto")
    return piece


def set_manual(db: Session, actor: Actor, piece_id: uuid.UUID, expected: datetime, colors: list[str]) -> Piece:
    palette = [c.strip().lower() for c in colors]
    if not MIN_COLORS <= len(palette) <= MAX_COLORS or not all(HEX_RE.fullmatch(c) for c in palette):
        raise ContentConflict("invalid_palette", f"Use {MIN_COLORS} to {MAX_COLORS} colours like #a1b2c3.", "colors")
    piece = _locked(db, Piece, piece_id, expected, "piece")
    _store(db, actor, piece, palette, "manual")
    return piece


def fill_if_missing(db: Session, actor: Actor, media_root: Path | None, piece_id: uuid.UUID) -> None:
    """After a photo upload: give the piece an automatic palette if it has
    none yet. Best effort, never fails the upload."""
    # No version bump on purpose: the person who just uploaded keeps the piece
    # version they loaded, so their next save or publish is not refused as
    # stale. Safe because content edits never write visual_theme.
    piece = db.execute(select(Piece).where(Piece.id == piece_id).with_for_update()).scalar_one_or_none()
    if piece is None or (piece.visual_theme or {}).get("palette"):
        db.rollback()
        return
    try:
        _store(db, actor, piece, _palette_from_cover(db, media_root, piece.id), "auto", touch=False)
    except ContentConflict:
        db.rollback()
