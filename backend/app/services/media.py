"""Gestión phase 4: media uploads for artisans and pieces (docs/MEDIA.md).

An upload is split in two copies:
- the original, byte for byte, in ``originales/{artisan}/{_artesano|piece}/``
  (0600, never served): field material that cannot be taken again;
- a published derivative in ``publico/{artesanos|piezas}/{slug}/{role}-{nn}.{ext}``,
  the only file /media/ can serve.

What reaches ``publico/``:
- images are decoded and re-encoded (Pillow): EXIF (GPS included) dropped,
  rotation applied, longest side <= 1600 px, progressive JPEG quality 80;
- MP4 videos are not re-encoded, so they are refused (not cleaned) when they
  carry an audio track or a location atom: the operator exports them again;
- GLB models are checked (glTF 2.0 binary header, declared length) and kept.

The type is sniffed from the bytes, never taken from the client. A published
file is never overwritten: a new upload of the same role gets the next number
(Cloudflare and browsers cache /media/ as immutable). Nothing is deleted:
archiving hides an asset from the public API but leaves its file in place.

Files are written before the database row. When the insert fails both files
are removed; a crash in between leaves an unreferenced file, never a row
without its file.
"""
from __future__ import annotations

import enum
import hashlib
import io
import os
import re
import struct
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.artisan import Artisan
from app.models.enums import PublicationStatus
from app.models.media_asset import MediaAsset, MediaAssetStatus, MediaRole, MediaType
from app.models.piece import Piece
from app.services.content import Actor, ContentConflict, ContentError, ContentNotFound, _audit, _locked, _touch

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_VIDEO_BYTES = 4 * 1024 * 1024
MAX_MODEL_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 1600
# A 50-megapixel photo is already beyond any phone camera; larger inputs are
# refused before decoding (decompression bombs).
MAX_IMAGE_PIXELS = 50_000_000
JPEG_QUALITY = 80

# Which roles each owner accepts, and which media types each role accepts.
ROLES: dict[str, dict[MediaRole, frozenset[MediaType]]] = {
    "artisan": {
        MediaRole.portrait: frozenset({MediaType.image}),
        MediaRole.process: frozenset({MediaType.image, MediaType.video}),
        MediaRole.gallery: frozenset({MediaType.image, MediaType.video}),
    },
    "piece": {
        MediaRole.hero: frozenset({MediaType.image}),
        MediaRole.gallery: frozenset({MediaType.image, MediaType.video}),
        MediaRole.detail: frozenset({MediaType.image}),
        MediaRole.process: frozenset({MediaType.image, MediaType.video}),
        MediaRole.model_3d: frozenset({MediaType.model_3d}),
    },
}
_FILE_PREFIX = {MediaRole.model_3d: "model"}
_OWNER_DIR = {"artisan": "artesanos", "piece": "piezas"}


class MediaError(ContentError):
    status_code = 422


class MediaTooLarge(ContentError):
    status_code = 413


class MediaUnavailable(ContentError):
    status_code = 503


class MediaAction(str, enum.Enum):
    archive = "archive"
    restore = "restore"


@dataclass(frozen=True)
class Derived:
    media_type: MediaType
    data: bytes
    extension: str
    original_extension: str
    format_metadata: dict[str, Any]


# --- sniffing and processing ---------------------------------------------------------


def _is_mp4(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == b"ftyp"


def _is_glb(data: bytes) -> bool:
    return data[:4] == b"glTF"


def process(data: bytes) -> Derived:
    """Sniff ``data`` and return what may be published. Raises MediaError
    (422) or MediaTooLarge (413) with a message the UI shows as is."""
    if not data:
        raise MediaError("empty_file", "The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaTooLarge("too_large", "The file is larger than 25 MB.")
    if _is_glb(data):
        return _process_glb(data)
    if _is_mp4(data):
        return _process_mp4(data)
    return _process_image(data)


def _process_image(data: bytes) -> Derived:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            if probe.format not in ("JPEG", "PNG", "WEBP"):
                raise MediaError("unsupported_type", "Upload a JPEG, PNG or WebP photo, an MP4 video or a GLB model.")
            width, height = probe.size
            if width * height > MAX_IMAGE_PIXELS:
                raise MediaError("image_too_large", "The photo has more than 50 megapixels.")
            source_format = probe.format
            image = Image.open(io.BytesIO(data))
            image.load()
    except MediaError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombWarning, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise MediaError("unsupported_type", "Upload a JPEG, PNG or WebP photo, an MP4 video or a GLB model.") from None

    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    elif image.mode != "RGB":
        image = image.convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    # No exif=, no xmp=: nothing from the original's metadata is written.
    image.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[source_format]
    return Derived(
        media_type=MediaType.image,
        data=out.getvalue(),
        extension="jpg",
        original_extension=extension,
        format_metadata={"width": image.width, "height": image.height, "mime_type": "image/jpeg"},
    )


def _boxes(data: bytes, start: int, end: int):
    """ISO BMFF boxes in data[start:end] as (type, payload_start, box_end)."""
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        header = 8
        if size == 1:
            if pos + 16 > end:
                raise MediaError("invalid_video", "The video file is damaged.")
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise MediaError("invalid_video", "The video file is damaged.")
        yield kind, pos + header, pos + size
        pos += size


def _child(data: bytes, start: int, end: int, kind: bytes):
    for box, payload, box_end in _boxes(data, start, end):
        if box == kind:
            return payload, box_end
    return None


def _process_mp4(data: bytes) -> Derived:
    if len(data) > MAX_VIDEO_BYTES:
        raise MediaTooLarge("too_large", "A video must be 4 MB or less.")
    moov = _child(data, 0, len(data), b"moov")
    if moov is None:
        raise MediaError("invalid_video", "The video file is damaged.")
    moov_bytes = data[moov[0]:moov[1]]
    if b"\xa9xyz" in moov_bytes or b"location.ISO6709" in moov_bytes:
        raise MediaError("video_has_location", "The video carries its GPS location. Export it again without location.")

    width = height = None
    for kind, payload, box_end in _boxes(data, *moov):
        if kind != b"trak":
            continue
        mdia = _child(data, payload, box_end, b"mdia")
        hdlr = mdia and _child(data, mdia[0], mdia[1], b"hdlr")
        handler = data[hdlr[0] + 8:hdlr[0] + 12] if hdlr else b""
        if handler == b"soun":
            raise MediaError("video_has_audio", "The video has sound. Export it again without the audio track.")
        tkhd = _child(data, payload, box_end, b"tkhd")
        if handler == b"vide" and tkhd and tkhd[1] - tkhd[0] >= 8:
            w, h = struct.unpack(">II", data[tkhd[1] - 8:tkhd[1]])
            width, height = w >> 16, h >> 16

    duration = None
    mvhd = _child(data, *moov, b"mvhd")
    if mvhd:
        version = data[mvhd[0]]
        if version == 1 and mvhd[1] - mvhd[0] >= 32:
            timescale, length = struct.unpack(">IQ", data[mvhd[0] + 20:mvhd[0] + 32])
        elif mvhd[1] - mvhd[0] >= 20:
            timescale, length = struct.unpack(">II", data[mvhd[0] + 12:mvhd[0] + 20])
        else:
            timescale = length = 0
        if timescale:
            duration = round(length / timescale, 2)
    return Derived(
        media_type=MediaType.video,
        data=data,
        extension="mp4",
        original_extension="mp4",
        format_metadata={"width": width, "height": height, "duration_seconds": duration, "mime_type": "video/mp4"},
    )


def _process_glb(data: bytes) -> Derived:
    if len(data) > MAX_MODEL_BYTES:
        raise MediaTooLarge("too_large", "A 3D model must be 8 MB or less.")
    if len(data) < 12:
        raise MediaError("invalid_model", "The 3D model is not a valid GLB file.")
    version, length = struct.unpack("<II", data[4:12])
    if version != 2 or length != len(data):
        raise MediaError("invalid_model", "The 3D model is not a valid GLB (glTF 2.0) file.")
    return Derived(
        media_type=MediaType.model_3d,
        data=data,
        extension="glb",
        original_extension="glb",
        format_metadata={"format": "glb", "file_size_bytes": len(data)},
    )


# --- files -------------------------------------------------------------------------


def _write_new(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _publish(folder: Path, prefix: str, extension: str, data: bytes) -> Path:
    folder.mkdir(mode=0o755, parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)\.[a-z0-9]+$")
    taken = [int(m.group(1)) for name in os.listdir(folder) if (m := pattern.match(name))]
    number = max(taken, default=0) + 1
    while True:
        path = folder / f"{prefix}-{number:02d}.{extension}"
        try:
            _write_new(path, data, 0o644)
            return path
        except FileExistsError:
            number += 1


def _keep_original(folder: Path, extension: str, data: bytes) -> Path:
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"{stamp}-{uuid.uuid4().hex[:8]}.{extension}"
    _write_new(path, data, 0o600)
    return path


# --- writes ------------------------------------------------------------------------


def _owner(db: Session, owner_type: str, owner_id: uuid.UUID) -> tuple[Any, str, str]:
    """(row, public folder slug, originals subpath) of an upload's owner."""
    if owner_type == "artisan":
        artisan = db.get(Artisan, owner_id)
        if artisan is None:
            raise ContentNotFound("not_found", "The requested resource does not exist.")
        if artisan.publication_status == PublicationStatus.archived:
            raise ContentConflict("archived", "Restore the artisan before adding media.")
        return artisan, artisan.slug, f"{artisan.slug}/_artesano"
    piece = db.get(Piece, owner_id)
    if piece is None:
        raise ContentNotFound("not_found", "The requested resource does not exist.")
    if piece.publication_status == PublicationStatus.archived:
        raise ContentConflict("archived", "Restore the piece before adding media.")
    artisan = db.get(Artisan, piece.artisan_id)
    return piece, piece.slug, f"{artisan.slug}/{piece.slug}"


def upload(db: Session, actor: Actor, media_root: Path, owner_type: str, owner_id: uuid.UUID,
           role: MediaRole, alt_text: str | None, data: bytes) -> MediaAsset:
    allowed = ROLES[owner_type]
    if role not in allowed:
        raise MediaError("invalid_role", f"The role {role.value} is not available here.", "role")
    owner, slug, originals_subpath = _owner(db, owner_type, owner_id)
    derived = process(data)
    if derived.media_type not in allowed[role]:
        raise MediaError("wrong_type_for_role", f"The role {role.value} does not take this kind of file.", "role")
    if derived.media_type == MediaType.image and not alt_text:
        raise MediaError("alt_text_required", "A photo needs a description (alternative text).", "alt_text")

    public_root = media_root / "publico"
    folder = public_root / _OWNER_DIR[owner_type] / slug
    written: list[Path] = []
    try:
        original = _keep_original(media_root / "originales" / originals_subpath, derived.original_extension, data)
        written.append(original)
        published = _publish(folder, _FILE_PREFIX.get(role, role.value), derived.extension, derived.data)
        written.append(published)

        owner_column = MediaAsset.artisan_id if owner_type == "artisan" else MediaAsset.piece_id
        last = db.execute(select(func.max(MediaAsset.position)).where(owner_column == owner_id)).scalar_one()
        asset = MediaAsset(
            artisan_id=owner_id if owner_type == "artisan" else None,
            piece_id=owner_id if owner_type == "piece" else None,
            media_type=derived.media_type,
            role=role,
            storage_path=published.relative_to(public_root).as_posix(),
            alt_text=alt_text,
            position=0 if last is None else last + 1,
            format_metadata=derived.format_metadata,
            status=MediaAssetStatus.active,
        )
        db.add(asset)
        db.flush()
        _audit(db, actor, "media_asset", asset.id, "media.uploaded", {
            "owner_type": owner_type,
            "owner_id": str(owner_id),
            "role": role.value,
            "media_type": derived.media_type.value,
            "storage_path": asset.storage_path,
            "bytes": len(derived.data),
            "original_sha256": hashlib.sha256(data).hexdigest(),
        })
        db.commit()
    except BaseException:
        db.rollback()
        for path in written:
            path.unlink(missing_ok=True)
        raise
    db.refresh(asset)
    return asset


def update(db: Session, actor: Actor, media_id: uuid.UUID, expected: datetime, changes: dict[str, Any]) -> MediaAsset:
    asset = _locked(db, MediaAsset, media_id, expected, "media item")
    if "alt_text" in changes and changes["alt_text"] is None and asset.media_type == MediaType.image:
        raise MediaError("alt_text_required", "A photo needs a description (alternative text).", "alt_text")
    diff = {}
    for field in ("alt_text", "position"):
        if field in changes and getattr(asset, field) != changes[field]:
            diff[field] = {"from": getattr(asset, field), "to": changes[field]}
            setattr(asset, field, changes[field])
    if diff:
        _touch(db, asset)
        _audit(db, actor, "media_asset", asset.id, "media.updated", {"changes": diff})
    db.commit()
    db.refresh(asset)
    return asset


def transition(db: Session, actor: Actor, media_id: uuid.UUID, expected: datetime, action: str) -> MediaAsset:
    asset = _locked(db, MediaAsset, media_id, expected, "media item")
    if action == "archive":
        if asset.status == MediaAssetStatus.archived:
            raise ContentConflict("invalid_transition", "The media item is already archived.")
        target = MediaAssetStatus.archived
    else:
        if asset.status == MediaAssetStatus.active:
            raise ContentConflict("invalid_transition", "The media item is not archived.")
        target = MediaAssetStatus.active
    before = asset.status
    asset.status = target
    _touch(db, asset)
    _audit(db, actor, "media_asset", asset.id, f"media.{'archived' if action == 'archive' else 'restored'}",
           {"from": before.value, "to": target.value})
    db.commit()
    db.refresh(asset)
    return asset


def owner_of(asset: MediaAsset) -> tuple[str, uuid.UUID]:
    if asset.piece_id is not None:
        return "piece", asset.piece_id
    return "artisan", asset.artisan_id


def media_unavailable() -> MediaUnavailable:
    return MediaUnavailable("media_not_configured", "Media storage is not configured on this server.")
