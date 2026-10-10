"""P-029: fixed images of the public site that Gestión → Hero can replace.

Each *slot* is one place on the site (today only the home's collection entry).
A slot without a row keeps the provisional image built into the site. Uploading
a photo crops it to the slot's proportion (centred), caps its size and writes
AVIF + WebP + JPEG named by content hash under ``publico/sitio/{slot}/``; the
files of a replaced image are removed only after the new row is saved. No
metadata of the original (EXIF, location) is written.
"""
from __future__ import annotations

import hashlib
import io
import os
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError, features
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.audit_event import AuditActorType, AuditEvent, AuditResult
from app.models.site_image import SiteImage
from app.services.content import Actor, ContentError, ContentNotFound

MAX_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 50_000_000
MIN_WIDTH = 600
JPEG_QUALITY = 82
WEBP_QUALITY = 80
AVIF_QUALITY = 60
_ALLOWED = ("JPEG", "PNG", "WEBP")
_TYPE_MESSAGE = "Sube una foto JPEG, PNG o WebP."


@dataclass(frozen=True)
class Slot:
    key: str
    label: str
    ratio: tuple[int, int]  # width : height
    max_width: int


SLOTS: dict[str, Slot] = {
    "collection-entry": Slot("collection-entry", "Entrada a la colección", (4, 5), 1200),
}


class SiteImageInvalid(ContentError):
    status_code = 422


class SiteImageTooLarge(ContentError):
    status_code = 413


class SiteImageUnavailable(ContentError):
    status_code = 503


def slot_of(key: str) -> Slot:
    slot = SLOTS.get(key)
    if slot is None:
        raise ContentNotFound("not_found", "Esa imagen del sitio no existe.")
    return slot


@dataclass(frozen=True)
class Rendered:
    jpg: bytes
    webp: bytes
    avif: bytes
    width: int
    height: int


def render(data: bytes, slot: Slot) -> Rendered:
    """Validates the photo and returns its three files. Raises SiteImageInvalid / TooLarge."""
    if not data:
        raise SiteImageInvalid("empty_file", "El archivo está vacío.")
    if len(data) > MAX_BYTES:
        raise SiteImageTooLarge("too_large", "La foto pesa más de 25 MB.")
    if not features.check("avif"):
        raise SiteImageUnavailable("avif_unavailable", "El servidor no puede crear imágenes AVIF.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            if probe.format not in _ALLOWED:
                raise SiteImageInvalid("unsupported_type", _TYPE_MESSAGE, "file")
            if probe.size[0] * probe.size[1] > MAX_PIXELS:
                raise SiteImageInvalid("image_too_large", "La foto tiene más de 50 megapíxeles.", "file")
            image = Image.open(io.BytesIO(data))
            image.load()
    except ContentError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombWarning, Image.DecompressionBombError, OSError,
            SyntaxError, ValueError):
        raise SiteImageInvalid("unsupported_type", _TYPE_MESSAGE, "file") from None

    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    elif image.mode != "RGB":
        image = image.convert("RGB")

    rw, rh = slot.ratio
    crop_w = min(image.width, image.height * rw / rh)
    if crop_w < MIN_WIDTH:
        raise SiteImageInvalid("too_small", f"La foto es muy chica: necesita al menos {MIN_WIDTH} px de ancho "
                                            f"en proporción {rw}:{rh}.", "file")
    width = int(min(slot.max_width, crop_w))
    height = round(width * rh / rw)
    image = ImageOps.fit(image, (width, height), Image.Resampling.LANCZOS, centering=(0.5, 0.5))

    def save(**options: Any) -> bytes:
        out = io.BytesIO()
        image.save(out, **options)  # no exif=, no xmp=: nothing of the original's metadata
        return out.getvalue()

    return Rendered(
        jpg=save(format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True),
        webp=save(format="WEBP", quality=WEBP_QUALITY, method=4),
        avif=save(format="AVIF", quality=AVIF_QUALITY, speed=6),
        width=width,
        height=height,
    )


def _store(public_root: Path, slot: Slot, rendered: Rendered) -> dict[str, str]:
    stem = hashlib.sha256(rendered.jpg).hexdigest()[:12]
    folder = public_root / "sitio" / slot.key
    folder.mkdir(mode=0o755, parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for extension, payload in (("avif", rendered.avif), ("webp", rendered.webp), ("jpg", rendered.jpg)):
        target = folder / f"{stem}.{extension}"
        if not target.exists():  # named by content: an existing file is the same photo
            temp = folder / f".{stem}.{extension}.{uuid.uuid4().hex[:6]}.tmp"
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            os.replace(temp, target)
            target.chmod(0o644)
        paths[extension] = f"sitio/{slot.key}/{stem}.{extension}"
    return paths


def _remove(public_root: Path, paths: list[str]) -> None:
    for path in paths:
        (public_root / path).unlink(missing_ok=True)


def _audit(db: Session, actor: Actor, slot: str, action: str, metadata: dict[str, Any] | None = None) -> None:
    db.add(AuditEvent(occurred_at=func.clock_timestamp(), actor_type=AuditActorType.admin_user,
                      actor_email=actor.identity.email, entity_type="site_image",
                      entity_id=uuid.uuid5(uuid.NAMESPACE_URL, f"artesanfc:site_image:{slot}"),
                      action=f"site_image.{action}", result=AuditResult.success, ip_address=actor.ip_address,
                      event_metadata={"slot": slot, **(metadata or {})}))


def all_images(db: Session) -> dict[str, SiteImage]:
    return {row.slot: row for row in db.query(SiteImage).all()}


def set_image(db: Session, actor: Actor, public_root: Path, key: str, data: bytes) -> SiteImage:
    slot = slot_of(key)
    rendered = render(data, slot)
    paths = _store(public_root, slot, rendered)
    row = db.get(SiteImage, slot.key, with_for_update=True)
    old = [row.avif, row.webp, row.jpg] if row else []
    if row is None:
        row = SiteImage(slot=slot.key, avif=paths["avif"], webp=paths["webp"], jpg=paths["jpg"],
                        width=rendered.width, height=rendered.height)
        db.add(row)
    else:
        row.avif, row.webp, row.jpg = paths["avif"], paths["webp"], paths["jpg"]
        row.width, row.height = rendered.width, rendered.height
    row.updated_by = actor.identity.email
    _audit(db, actor, slot.key, "updated", {"bytes": len(data), "width": rendered.width, "height": rendered.height})
    db.commit()
    _remove(public_root, [p for p in old if p not in paths.values()])
    return row


def clear(db: Session, actor: Actor, public_root: Path | None, key: str) -> None:
    """Goes back to the provisional image built into the site."""
    slot = slot_of(key)
    row = db.get(SiteImage, slot.key, with_for_update=True)
    if row is None:
        return
    old = [row.avif, row.webp, row.jpg]
    db.delete(row)
    _audit(db, actor, slot.key, "cleared")
    db.commit()
    if public_root is not None:
        _remove(public_root, old)


def public_view(row: SiteImage) -> dict[str, Any]:
    return {"avif": f"/media/{row.avif}", "webp": f"/media/{row.webp}", "jpg": f"/media/{row.jpg}",
            "width": row.width, "height": row.height}
