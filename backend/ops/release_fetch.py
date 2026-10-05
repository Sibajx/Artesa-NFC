"""Download a release from GitHub and activate the Gestión UI (docs/DEPLOYMENT.md §11.8).

Release CI publishes every validated ``main`` commit as a GitHub Release
``release-<release_id>`` with four assets: the backend artifact, its sidecar,
``admin-ui-<commit12>.tgz`` and its sidecar. The repository is public, so the
server needs no credential to read them.

``fetch`` only puts files in ``incoming/``. It never prepares, deploys or
activates anything: ``prepare`` re-verifies the artifact in full and
``deploy --expect-commit`` binds it to the commit the operator expects. That
is the same chain of trust as copying the files with scp from a machine that
downloaded them from GitHub. HTTPS is verified against the system CAs, and
redirects must stay on HTTPS. If the network path to GitHub fails (for
example, a TLS-inspecting firewall), nothing is written and the scp route
still works.

``activate_ui`` replaces the five manual commands that switched
``shared/admin-ui/current``: it verifies the tarball against its sidecar,
extracts only regular files and directories with safe names into a private
staging directory, renames it into place and swaps the symlink atomically.
A directory that already exists is reused, which also serves as the
rollback (activate the previous commit again).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import release_common as rc

REPOSITORY = "Sibajx/Artesa-NFC"
API = f"https://api.github.com/repos/{REPOSITORY}/releases"
TAG_PREFIX = "release-"
MAX_ARTIFACT = 64 << 20
MAX_UI = 32 << 20
MAX_SMALL = 1 << 20
TIMEOUT = 60.0
_COMMIT12 = re.compile(r"[0-9a-f]{12}")

Download = Callable[[str, int], bytes]


class FetchError(rc.OpsError):
    def __init__(self, message: str) -> None:
        super().__init__(rc.Exit.ARTIFACT_INVALID, message)


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        if not newurl.startswith("https://"):
            raise urllib.error.URLError("redirect away from HTTPS refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_download(url: str, limit: int) -> bytes:
    """GET ``url`` over verified HTTPS; at most ``limit`` bytes."""
    if not url.startswith("https://"):
        raise FetchError("only https:// URLs are allowed")
    opener = urllib.request.build_opener(_HttpsOnlyRedirects())
    request = urllib.request.Request(url, headers={
        "User-Agent": f"artesa-deploy/{rc.TOOL_VERSION}",
        "Accept": "application/octet-stream" if "/assets/" in url else "application/vnd.github+json",
    })
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        raise FetchError(f"GitHub answered HTTP {exc.code} for {url.split('?')[0]}") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise FetchError(f"cannot reach GitHub ({type(reason).__name__}: {str(reason)[:120]}); "
                         "copy the files with scp instead (docs/DEPLOYMENT.md §11.8)") from None
    if len(data) > limit:
        raise FetchError(f"download larger than {limit} bytes refused")
    return data


@dataclass(frozen=True)
class Fetched:
    release_id: str
    commit12: str
    artifact: Path
    ui: Path | None
    reused: bool


def _parse_sidecar(data: bytes, expected_name: str) -> str:
    try:
        text = data.decode("ascii").strip()
    except UnicodeDecodeError:
        raise FetchError(f"{expected_name}.sha256 is not ASCII") from None
    parts = text.split()
    if len(parts) != 2 or parts[1].lstrip("*") != expected_name or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
        raise FetchError(f"{expected_name}.sha256 does not name {expected_name} with one SHA-256")
    return parts[0]


def _release_json(download: Download, release_id: str | None) -> dict:
    url = f"{API}/latest" if release_id is None else f"{API}/tags/{TAG_PREFIX}{rc.validate_release_id(release_id)}"
    try:
        doc = json.loads(download(url, MAX_SMALL).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FetchError("GitHub answered something that is not JSON") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("tag_name"), str) or not isinstance(doc.get("assets"), list):
        raise FetchError("unexpected release document from GitHub")
    return doc


def _write_new(path: Path, data: bytes, mode: int) -> bool:
    """Write atomically; True if written, False if an identical file exists.
    A different existing file is never overwritten."""
    if path.exists() or path.is_symlink():
        if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
            return False
        raise FetchError(f"{path.name} already exists in {path.parent.name}/ with different content; inspect it and remove it by hand")
    tmp = path.with_name(f".{path.name}.part-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return True


def fetch(incoming: Path, release_id: str | None, download: Download = https_download) -> Fetched:
    """Download the release (``None``: the latest) and its Gestión UI into
    ``incoming``, each checked against its sidecar before anything is kept."""
    doc = _release_json(download, release_id)
    tag = doc["tag_name"]
    if not tag.startswith(TAG_PREFIX):
        raise FetchError(f"release tag {tag!r} does not start with {TAG_PREFIX!r}")
    rid = rc.validate_release_id(tag[len(TAG_PREFIX):])
    if release_id is not None and rid != release_id:
        raise FetchError(f"GitHub returned {rid}, not the requested {release_id}")
    commit12 = rid.rsplit("-", 1)[1]
    assets = {a.get("name"): a.get("url") for a in doc["assets"] if isinstance(a, dict)}
    artifact_name = rc.artifact_filename(rid)
    ui_name = f"admin-ui-{commit12}.tgz"
    for needed in (artifact_name, f"{artifact_name}.sha256"):
        if not isinstance(assets.get(needed), str):
            raise FetchError(f"release {rid} has no asset {needed}")

    def get(name: str, limit: int) -> tuple[bytes, bytes]:
        sidecar = download(assets[f"{name}.sha256"], MAX_SMALL)
        expected = _parse_sidecar(sidecar, name)
        data = download(assets[name], limit)
        if hashlib.sha256(data).hexdigest() != expected:
            raise FetchError(f"{name} does not match its published SHA-256; nothing was kept")
        return data, sidecar

    artifact, artifact_sidecar = get(artifact_name, MAX_ARTIFACT)
    ui = None
    if isinstance(assets.get(ui_name), str) and isinstance(assets.get(f"{ui_name}.sha256"), str):
        ui = get(ui_name, MAX_UI)
    written = [
        _write_new(incoming / artifact_name, artifact, 0o640),
        _write_new(incoming / f"{artifact_name}.sha256", artifact_sidecar, 0o640),
    ]
    if ui is not None:
        written += [_write_new(incoming / ui_name, ui[0], 0o640), _write_new(incoming / f"{ui_name}.sha256", ui[1], 0o640)]
    return Fetched(rid, commit12, incoming / artifact_name, (incoming / ui_name) if ui is not None else None, not any(written))


# --- Gestión UI -------------------------------------------------------------------------------------


def validate_commit12(value: str) -> str:
    if not isinstance(value, str) or not _COMMIT12.fullmatch(value):
        raise rc.OpsError(rc.Exit.USAGE, "the UI version is the 12-hex commit (e.g. 14b3cf0ff994)")
    return value


def ui_dirs(ui_root: Path) -> tuple[str | None, list[str]]:
    """(the commit ``current`` points at, every installed commit directory)."""
    current = None
    link = ui_root / "current"
    if link.is_symlink():
        target = os.readlink(link)
        current = target if _COMMIT12.fullmatch(target) else None
    installed = sorted(p.name for p in ui_root.iterdir() if p.is_dir() and not p.is_symlink() and _COMMIT12.fullmatch(p.name)) \
        if ui_root.is_dir() else []
    return current, installed


def _extract(tgz: Path, dest: Path) -> int:
    count = 0
    with tarfile.open(tgz, mode="r:gz") as tar:
        for member in tar:
            name = PurePosixPath(member.name)
            parts = [p for p in name.parts if p not in (".", "")]
            if not parts:
                continue
            if name.is_absolute() or ".." in parts or any(p.startswith(".") for p in parts):
                raise FetchError(f"unsafe path in the UI tarball: {member.name!r}")
            target = dest.joinpath(*parts)
            if member.isdir():
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
            elif member.isreg():
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                source = tar.extractfile(member)
                with open(target, "xb") as handle:
                    shutil.copyfileobj(source, handle)
                os.chmod(target, 0o644)
                count += 1
            else:
                raise FetchError(f"the UI tarball holds a link or special file: {member.name!r}")
    return count


def activate_ui(ui_root: Path, incoming: Path, commit12: str) -> tuple[str | None, str, int | None]:
    """Point ``ui_root/current`` at ``commit12``. Returns (previous, new,
    files extracted or None when the directory was reused)."""
    validate_commit12(commit12)
    ui_root.mkdir(mode=0o755, exist_ok=True)
    previous, _ = ui_dirs(ui_root)
    final = ui_root / commit12
    extracted = None
    if not final.is_dir():
        tgz = incoming / f"admin-ui-{commit12}.tgz"
        sidecar = incoming / f"admin-ui-{commit12}.tgz.sha256"
        if not tgz.is_file() or not sidecar.is_file():
            raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, f"no installed UI {commit12} and no {tgz.name} (+ .sha256) in incoming/; "
                                                        "run `artesa-deploy fetch` first or copy them with scp")
        expected = _parse_sidecar(sidecar.read_bytes(), tgz.name)
        if rc.sha256_file(str(tgz)) != expected:
            raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, f"{tgz.name} does not match its .sha256")
        staging = ui_root / f".{commit12}.tmp-{os.getpid()}"
        os.mkdir(staging, 0o700)
        try:
            extracted = _extract(tgz, staging)
            if not (staging / "index.html").is_file():
                raise FetchError("the UI tarball has no index.html at its root")
            os.chmod(staging, 0o755)
            os.rename(staging, final)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    elif not (final / "index.html").is_file():
        raise rc.OpsError(rc.Exit.ARTIFACT_INVALID, f"shared/admin-ui/{commit12} exists but has no index.html; inspect it by hand")
    tmp = ui_root / f".current.tmp-{os.getpid()}"
    os.symlink(commit12, tmp)
    os.replace(tmp, ui_root / "current")
    return previous, commit12, extracted


def describe_ui(ui_root: Path) -> str:
    current, installed = ui_dirs(ui_root)
    if current is None:
        return "Gestión UI: no current version"
    others = [c for c in installed if c != current]
    return f"Gestión UI: current {current}" + (f" (also installed: {', '.join(others[-3:])})" if others else "")
