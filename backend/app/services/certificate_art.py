"""ADR-030 phase 5b: storing the team's artwork for original certificates.

Every upload is decoded and re-encoded with Pillow, so nothing of the original
file survives but its pixels (no metadata, no trailing data). Images with
transparency stay PNG (logos, line art); opaque ones become JPEG to keep the
certificate light. The longest side is at most 1000 px: the sheet is
1200 x 1600 and the art never fills more than its width.
"""
from __future__ import annotations

import base64
import hashlib
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.models.certificate_art import CertificateArt
from app.services.content import ContentError

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_SOURCE_PIXELS = 50_000_000
MAX_SIDE = 1000


class ArtError(ContentError):
    status_code = 422


def _decode(data: bytes) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            if probe.format not in ("PNG", "JPEG", "WEBP"):
                raise ArtError("unsupported_type", "Upload the artwork as PNG, JPEG or WebP.")
            if probe.size[0] * probe.size[1] > MAX_SOURCE_PIXELS:
                raise ArtError("image_too_large", "The artwork has more than 50 megapixels.")
            image = Image.open(io.BytesIO(data))
            image.load()
    except ArtError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombWarning, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ArtError("unsupported_type", "Upload the artwork as PNG, JPEG or WebP.") from None
    return ImageOps.exif_transpose(image)


def store(db: Session, uploaded_by: str, data: bytes) -> CertificateArt:
    if not data:
        raise ArtError("empty_file", "The file is empty.")
    image = _decode(data)
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
    image = image.convert("RGBA" if has_alpha else "RGB")
    if has_alpha and image.getchannel("A").getextrema() == (255, 255):
        image, has_alpha = image.convert("RGB"), False
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    if has_alpha:
        image.save(out, "PNG", optimize=True)
        mime = "image/png"
    else:
        image.save(out, "JPEG", quality=88, optimize=True, progressive=True)
        mime = "image/jpeg"
    content = out.getvalue()
    sha = hashlib.sha256(content).hexdigest()
    existing = db.get(CertificateArt, sha)
    if existing is not None:
        return existing
    art = CertificateArt(sha256=sha, mime_type=mime, content=content, width=image.width, height=image.height,
                         uploaded_by=uploaded_by)
    db.add(art)
    db.commit()
    return art


def data_uri(db: Session, sha: str | None) -> str | None:
    """The artwork as a data: URI for the renderer, or None."""
    if not sha:
        return None
    art = db.get(CertificateArt, sha)
    if art is None:
        return None
    return f"data:{art.mime_type};base64,{base64.b64encode(art.content).decode('ascii')}"


def exists(db: Session, sha: str) -> bool:
    return db.get(CertificateArt, sha) is not None
