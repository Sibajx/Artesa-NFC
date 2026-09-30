"""Serves /media/ from MEDIA_ROOT/publico (docs/MEDIA.md section 4, option A).

Only paths shaped like what the upload service writes are served:
``{artesanos|piezas|sitio}/{slug}/{name}.{ext}``, lowercase, with an allowed
extension. Anything else (``..``, encoded separators, hidden files, other
extensions, originales/) is a 404 before the filesystem is touched, and the
resolved file must still sit under publico/ as a regular file (no symlink
out).

Published files never change (a new version gets a new name), so they are
cacheable for a year and marked immutable. ``Access-Control-Allow-Origin: *``
is deliberate and limited to these public bytes: <model-viewer> fetches the
GLB with CORS, and Cloudflare caches one copy per URL regardless of
``Vary: Origin``, so an origin-specific header would be served to the wrong
site. No credentials are involved. This sits outside CORSMiddleware so the
two never mix.

Only a single, simple byte range reaches FileResponse (``bytes=a-b``,
``bytes=a-`` or ``bytes=-n``); any other Range header is dropped and the
whole file is served (200). Starlette 0.48's FileResponse merged multiple
ranges in quadratic time (upstream advisory, fixed in 0.49.1); the stack is
on Starlette 1.x since issue #121, and this stays as defence in depth:
nothing here needs multipart ranges -- Safari needs a single range to play a
video.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from starlette.responses import FileResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings
from app.core.errors import error_response

MEDIA_PREFIX = "/media/"
_SEGMENT = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_MEDIA_PATH_RE = re.compile(
    rf"^(?:artesanos|piezas|sitio)/{_SEGMENT}/{_SEGMENT}\.(jpg|jpeg|webp|avif|png|mp4|webm|glb)$"
)
CONTENT_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "avif": "image/avif", "png": "image/png",
    "mp4": "video/mp4", "webm": "video/webm", "glb": "model/gltf-binary",
}
_SINGLE_RANGE_RE = re.compile(r"bytes=(?:\d{1,15}-\d{0,15}|-\d{1,15})")
MEDIA_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
    "Access-Control-Allow-Origin": "*",
    "Cross-Origin-Resource-Policy": "cross-origin",
}


def _only_simple_range(scope: Scope) -> Scope:
    headers = scope["headers"]
    ranges = [value for name, value in headers if name == b"range"]
    if not ranges:
        return scope
    if len(ranges) == 1 and _SINGLE_RANGE_RE.fullmatch(ranges[0].decode("latin-1").strip()):
        return scope
    return {**scope, "headers": [(name, value) for name, value in headers if name != b"range"]}


def resolve_media_path(public_root: Path, relative: str) -> Path | None:
    match = _MEDIA_PATH_RE.fullmatch(relative)
    if match is None:
        return None
    candidate = public_root / relative
    try:
        info = os.lstat(candidate)
        real = candidate.resolve(strict=True)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or not real.is_relative_to(public_root.resolve()):
        return None
    return real


class MediaFilesMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith(MEDIA_PREFIX):
            await self.app(scope, receive, send)
            return
        settings = get_settings()
        if scope["method"] not in ("GET", "HEAD"):
            await error_response(405)(scope, receive, send)
            return
        found = None
        if settings.media_enabled:
            found = resolve_media_path(settings.media_public_dir, path[len(MEDIA_PREFIX):])
        if found is None:
            await error_response(404)(scope, receive, send)
            return
        extension = found.suffix[1:]
        response = FileResponse(found, media_type=CONTENT_TYPES[extension], headers=MEDIA_HEADERS)
        await response(_only_simple_range(scope), receive, send)
