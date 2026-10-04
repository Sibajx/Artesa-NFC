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
_SAMPLE_SIZE = 160
_CLUSTERS = 64
# Perceptual distances (CIE76 in Lab): below MERGE two clusters are one
# colour; a picked colour must differ from the others by at least DISTINCT.
_MERGE, _DISTINCT = 6.0, 22.0
# An accent: clearly coloured (Lab chroma) and visible, even if small.
_ACCENT_CHROMA, _ACCENT_MIN_SHARE = 32.0, 0.004
_LIGHT_MIN_L, _LIGHT_MIN_SHARE = 72.0, 0.004
_DARK_MAX_L, _DARK_MIN_SHARE = 28.0, 0.02
# The remaining slots only take colours with a real presence, not the soft
# edges between two picked colours.
_FILL_MIN_SHARE = 0.03

Rgb = tuple[int, int, int]


def _hex(rgb: Rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _lab(rgb: Rgb) -> tuple[float, float, float]:
    def linear(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


class _Cluster:
    __slots__ = ("weight", "sums", "rgb", "lab")

    def __init__(self) -> None:
        self.weight = 0.0
        self.sums = [0.0, 0.0, 0.0]
        self.rgb: Rgb = (0, 0, 0)
        self.lab = (0.0, 0.0, 0.0)

    def finish(self) -> "_Cluster":
        self.rgb = tuple(round(v / self.weight) for v in self.sums)  # type: ignore[assignment]
        self.lab = _lab(self.rgb)
        return self

    def absorb(self, other: "_Cluster") -> None:
        self.weight += other.weight
        self.sums = [a + b for a, b in zip(self.sums, other.sums)]
        self.finish()

    @property
    def chroma(self) -> float:
        return (self.lab[1] ** 2 + self.lab[2] ** 2) ** 0.5


def _delta(a: _Cluster, b: _Cluster) -> float:
    return sum((x - y) ** 2 for x, y in zip(a.lab, b.lab)) ** 0.5


def _clusters(image: Image.Image) -> list[_Cluster]:
    """Weighted colour clusters. The piece is usually in the middle of its
    photo and the background at the edges, so a pixel counts 1.0 in the
    centre, 0.1 at the middle of each edge and in the corners."""
    width, height = image.size
    indices = list(image.quantize(colors=_CLUSTERS, method=Image.Quantize.MEDIANCUT).getdata())
    pixels = list(image.getdata())
    clusters: dict[int, _Cluster] = {}
    for i, (index, rgb) in enumerate(zip(indices, pixels)):
        x, y = i % width, i // width
        dx, dy = (x + 0.5) / width * 2 - 1, (y + 0.5) / height * 2 - 1
        weight = max(0.1, 1.0 - 0.9 * (dx * dx + dy * dy))
        cluster = clusters.setdefault(index, _Cluster())
        cluster.weight += weight
        for k in range(3):
            cluster.sums[k] += rgb[k] * weight
    merged: list[_Cluster] = []
    for cluster in sorted((c.finish() for c in clusters.values()), key=lambda c: -c.weight):
        twin = next((m for m in merged if _delta(m, cluster) < _MERGE), None)
        if twin:
            twin.absorb(cluster)
        else:
            merged.append(cluster)
    total = sum(c.weight for c in merged)
    for c in merged:
        c.weight /= total  # from here on, weight = share of the (weighted) photo
    return merged


def extract(path: Path) -> list[str]:
    """3-5 colours that describe the piece, in this order: its main colour,
    its most vivid accent (even a small one, like the red of a mouth), its
    darkest and lightest tones, then the next most present. Raises
    ValueError for an unreadable image."""
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            # NEAREST samples real pixels; a smoothing resize would blend small
            # details (white teeth next to a red mouth) into colours the
            # piece does not have.
            scale = _SAMPLE_SIZE / max(image.size)
            if scale < 1:
                size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
                image = image.resize(size, Image.Resampling.NEAREST)
            clusters = _clusters(image)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("unreadable image") from exc

    picked: list[_Cluster] = []

    def take(candidates: list[_Cluster]) -> None:
        for c in candidates:
            if len(picked) < MAX_COLORS and all(_delta(c, p) >= _DISTINCT for p in picked):
                picked.append(c)
                return

    take(sorted(clusters, key=lambda c: -c.weight))
    take(sorted((c for c in clusters if c.chroma >= _ACCENT_CHROMA and c.weight >= _ACCENT_MIN_SHARE),
                key=lambda c: -(c.chroma * c.weight ** 0.5)))
    take(sorted((c for c in clusters if c.lab[0] <= _DARK_MAX_L and c.weight >= _DARK_MIN_SHARE),
                key=lambda c: c.lab[0]))
    take(sorted((c for c in clusters if c.lab[0] >= _LIGHT_MIN_L and c.weight >= _LIGHT_MIN_SHARE),
                key=lambda c: -c.lab[0]))
    for c in sorted((c for c in clusters if c.weight >= _FILL_MIN_SHARE), key=lambda c: -(c.weight * (1 + c.chroma / 40))):
        take([c])

    colors: list[Rgb] = [c.rgb for c in picked]
    # A nearly flat photo: complete with lighter and darker shades of the main colour.
    base = colors[0] if colors else (128, 128, 128)
    shade = 0.45
    while len(colors) < MIN_COLORS:
        factor = 1 + shade if len(colors) % 2 else 1 - shade
        colors.append(tuple(max(0, min(255, round(c * factor))) for c in base))  # type: ignore[arg-type]
        shade /= 2
    return [_hex(rgb) for rgb in colors]


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
