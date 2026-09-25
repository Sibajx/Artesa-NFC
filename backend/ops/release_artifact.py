"""Verification and safe extraction of release artifacts, plus drift detection
on an already-extracted release tree (N-08, ADR-027).

Nothing is extracted before the *whole* archive has been validated in memory:
sidecar checksum, member types/paths/modes/sizes, manifest, RELEASE.json,
lockfile, Alembic graph and migration ledger. Extraction is done by this
module (never ``tarfile.extractall``) and only writes regular files.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import stat
import tarfile
import zlib
from dataclasses import dataclass
from pathlib import Path

import release_common as rc

_SIDECAR_RE = re.compile(r"([0-9a-f]{64})  (\S+\.tar\.gz)\n?")
_NAME_ID_RE = re.compile(r"artesa-nfc-(?P<id>.+)\.tar\.gz")

# Runtime state that legitimately lives inside a prepared release directory
# but is not part of the artifact: the per-release venv, the prepared marker
# and interpreter bytecode.
_RELEASE_LOCAL_TOP = ("venv", ".prepared")


@dataclass
class VerifiedArtifact:
    release_id: str
    release: dict
    manifest: dict[str, str]
    manifest_bytes: bytes
    contents: dict[str, bytes]  # every member, incl. RELEASE.json / MANIFEST.sha256
    archive_sha256: str
    ledger: dict[str, dict]


def _fail(message: str) -> rc.OpsError:
    return rc.OpsError(rc.Exit.ARTIFACT_INVALID, message)


def release_id_from_filename(path: Path) -> str:
    match = _NAME_ID_RE.fullmatch(path.name)
    if not match:
        raise _fail("artifact file name must be artesa-nfc-<release id>.tar.gz")
    return rc.validate_release_id(match.group("id"))


def read_sidecar(artifact: Path) -> str:
    sidecar = artifact.with_name(artifact.name + ".sha256")
    try:
        st = os.lstat(sidecar)
    except FileNotFoundError:
        raise _fail("checksum sidecar (<artifact>.sha256) is missing") from None
    if not stat.S_ISREG(st.st_mode) or st.st_size > 512:
        raise _fail("checksum sidecar is not a small regular file")
    text = sidecar.read_text(encoding="ascii", errors="replace")
    match = _SIDECAR_RE.fullmatch(text)
    if not match or match.group(2) != artifact.name:
        raise _fail("checksum sidecar is malformed or names a different artifact")
    return match.group(1)


def verify_artifact(artifact: Path, *, expected_id: str | None = None) -> VerifiedArtifact:
    artifact = Path(artifact)
    try:
        st = os.lstat(artifact)
    except FileNotFoundError:
        raise _fail("artifact not found in incoming/") from None
    if not stat.S_ISREG(st.st_mode):
        raise _fail("artifact is not a regular file (symlink or other)")
    release_id = release_id_from_filename(artifact)
    if expected_id is not None and release_id != expected_id:
        raise _fail("artifact name does not match the requested release id")

    expected_sha = read_sidecar(artifact)
    actual_sha = rc.sha256_file(str(artifact))
    if actual_sha != expected_sha:
        raise _fail("artifact checksum does not match its sidecar (tampered or truncated)")

    contents = _read_members(artifact)
    for name in rc.GENERATED_FILES:
        if name not in contents:
            raise _fail(f"artifact has no {name}")

    manifest_bytes = contents["MANIFEST.sha256"]
    manifest = rc.parse_manifest(manifest_bytes)
    content = {name: data for name, data in contents.items() if name not in rc.GENERATED_FILES}
    if set(content) != set(manifest):
        extra = sorted(set(content) - set(manifest))[:3]
        gone = sorted(set(manifest) - set(content))[:3]
        raise _fail(f"archive and MANIFEST.sha256 disagree (unlisted: {extra}, missing: {gone})")
    for name, data in content.items():
        if rc.sha256_bytes(data) != manifest[name]:
            raise _fail(f"file does not match MANIFEST.sha256: {rc._short(name)}")
    for name in rc.REQUIRED_FILES:
        if name not in manifest:
            raise _fail(f"required file missing from artifact: {name}")

    try:
        release_obj = json.loads(contents["RELEASE.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _fail("RELEASE.json is not valid JSON") from None
    release = rc.validate_release_json(release_obj, expected_id=release_id)
    if release["artifact"]["content_sha256"] != rc.sha256_bytes(manifest_bytes):
        raise _fail("RELEASE.json content_sha256 does not match MANIFEST.sha256")
    if release["artifact"]["file_count"] != len(manifest):
        raise _fail("RELEASE.json file_count does not match MANIFEST.sha256")
    if release["deps"]["lockfile_sha256"] != manifest[rc.LOCK_PATH]:
        raise _fail("RELEASE.json lockfile_sha256 does not match the lockfile")
    rc.parse_lock(content[rc.LOCK_PATH].decode("utf-8"), target_python=release["build"]["target_python"])

    revisions = rc.parse_alembic_revisions({n: d for n, d in content.items() if n.startswith("alembic/versions/")})
    if revisions != release["alembic"]["revisions"] or rc.alembic_heads(revisions) != [release["alembic"]["head"]]:
        raise _fail("RELEASE.json alembic block does not match the migration files")
    ledger = rc.parse_ledger(content[rc.LEDGER_PATH])
    rc.check_ledger_complete(revisions, ledger)

    return VerifiedArtifact(release_id, release, manifest, manifest_bytes, contents, actual_sha, ledger)


def _read_members(artifact: Path) -> dict[str, bytes]:
    contents: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(artifact, mode="r:gz") as tar:
            for count, member in enumerate(tar, start=1):
                if count > rc.MAX_MEMBERS:
                    raise _fail("archive has too many members")
                name = rc.validate_member_path(member.name)
                if not member.isreg():
                    raise _fail(f"archive member is not a regular file (link/dir/device): {rc._short(name)}")
                if name in contents:
                    raise _fail("archive contains a duplicate member")
                if not (name in rc.GENERATED_FILES or rc.is_allowed_content_path(name)):
                    raise _fail(f"archive member outside the artifact roots: {rc._short(name)}")
                if member.mode & 0o7022 or member.mode & 0o400 == 0:
                    raise _fail(f"archive member has an unsafe mode: {rc._short(name)}")
                if member.size > rc.MAX_FILE_BYTES:
                    raise _fail(f"archive member too large: {rc._short(name)}")
                total += member.size
                if total > rc.MAX_TOTAL_BYTES:
                    raise _fail("archive expands beyond the allowed total size")
                handle = tar.extractfile(member)
                data = handle.read(member.size + 1) if handle else b""
                if len(data) != member.size:
                    raise _fail("archive member is truncated")
                rc.assert_no_secret_content(name, data)
                contents[name] = data
    except rc.OpsError:
        raise
    except (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile, OSError):
        raise _fail("archive is corrupt or truncated") from None
    return contents


def extract_artifact(verified: VerifiedArtifact, dest: Path) -> None:
    """Write a verified artifact into ``dest`` (which must not exist). Only
    called with the output of ``verify_artifact``: paths were validated there
    and are re-validated here as defence in depth."""
    dest = Path(dest)
    os.mkdir(dest, 0o755)
    for name, data in verified.contents.items():
        rc.validate_member_path(name)
        target = dest / name
        os.makedirs(target.parent, mode=0o755, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)


def verify_tree(release_dir: Path) -> list[str]:
    """Drift/identity check of an extracted release directory against its own
    MANIFEST.sha256 and RELEASE.json. Returns human-readable problems (empty
    when the tree is exactly the artifact). Detects modified, missing and
    unexpected files (the unrelated ``main.py`` of the 2026-09-21 incident),
    symlinks, hardlinks and unsafe modes."""
    release_dir = Path(release_dir)
    problems: list[str] = []
    try:
        manifest_bytes = (release_dir / "MANIFEST.sha256").read_bytes()
        manifest = rc.parse_manifest(manifest_bytes)
        release = rc.validate_release_json(
            json.loads((release_dir / "RELEASE.json").read_text(encoding="utf-8")), expected_id=release_dir.name
        )
        if release["artifact"]["content_sha256"] != rc.sha256_bytes(manifest_bytes):
            problems.append("MANIFEST.sha256 does not match RELEASE.json content_sha256")
    except (OSError, ValueError, rc.OpsError) as exc:
        return [f"release metadata unreadable or invalid: {getattr(exc, 'message', type(exc).__name__)}"]

    seen: set[str] = set()
    for current, dirs, files in os.walk(release_dir, followlinks=False):
        rel_dir = os.path.relpath(current, release_dir)
        top = "" if rel_dir == "." else rel_dir.split(os.sep, 1)[0]
        if top in _RELEASE_LOCAL_TOP:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d != "__pycache__" and not os.path.islink(os.path.join(current, d))]
        for d in os.listdir(current):
            if os.path.islink(os.path.join(current, d)) and os.path.isdir(os.path.join(current, d)) and d not in _RELEASE_LOCAL_TOP:
                problems.append(f"unexpected directory symlink: {os.path.join(rel_dir, d) if rel_dir != '.' else d}")
        for name in files:
            full = os.path.join(current, name)
            rel = name if rel_dir == "." else os.path.join(rel_dir, name).replace(os.sep, "/")
            if rel in _RELEASE_LOCAL_TOP:
                continue
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode):
                problems.append(f"unexpected symlink: {rel}")
                continue
            if not stat.S_ISREG(st.st_mode):
                problems.append(f"unexpected non-regular file: {rel}")
                continue
            if st.st_nlink > 1:
                problems.append(f"unexpected hardlink: {rel}")
            if st.st_mode & 0o7022:
                problems.append(f"unsafe mode {stat.S_IMODE(st.st_mode):o}: {rel}")
            if rel in rc.GENERATED_FILES:
                seen.add(rel)
                continue
            if rel not in manifest:
                problems.append(f"unexpected file not in manifest: {rel}")
                continue
            seen.add(rel)
            if rc.sha256_file(full) != manifest[rel]:
                problems.append(f"modified since extraction: {rel}")
    for rel in sorted(set(manifest) - seen):
        problems.append(f"missing file: {rel}")
    for name in rc.GENERATED_FILES:
        if name not in seen:
            problems.append(f"missing file: {name}")
    return problems
