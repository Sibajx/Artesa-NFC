"""Private photos (production steps, visits): evidence for the team, never public.

A photo is decoded and re-encoded as JPEG (Pillow; EXIF and GPS dropped, longest
side 1600 px) by the same code that prepares the public media, and written under
``MEDIA_ROOT/privado/<area>/<owner>/`` with owner-only permissions. It is only
ever read back through an authenticated admin endpoint; the path never leaves
the server. ``read`` refuses any path that is not inside ``privado/``.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from app.services import media

PRIVATE_DIR = "privado"
MAX_PHOTO_BYTES = media.MAX_UPLOAD_BYTES


def store(media_root: Path, area: str, owner: uuid.UUID, data: bytes) -> tuple[str, int, int]:
    """Re-encodes ``data`` and saves it. Returns (relative path, width, height).
    Raises MediaError / MediaTooLarge (the messages the UI shows) for a bad file."""
    if not data:
        raise media.MediaError("empty_file", "The file is empty.")
    if len(data) > MAX_PHOTO_BYTES:
        raise media.MediaTooLarge("too_large", "The file is larger than 25 MB.")
    derived = media._process_image(data)
    folder = media_root / PRIVATE_DIR / area / str(owner)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    media._write_new(folder / name, derived.data, 0o600)
    meta = derived.format_metadata
    return f"{PRIVATE_DIR}/{area}/{owner}/{name}", int(meta["width"]), int(meta["height"])


def resolve(media_root: Path, relative: str) -> Path | None:
    """The file for a stored path, or None when it is missing or outside privado/."""
    base = (media_root / PRIVATE_DIR).resolve()
    path = (media_root / relative).resolve()
    if base not in path.parents or not path.is_file():
        return None
    return path


def remove(media_root: Path, relative: str) -> None:
    path = resolve(media_root, relative)
    if path is not None:
        try:
            os.unlink(path)
        except OSError:
            pass
