"""Off-host copies of the media originals for artesa-backup (docs/MEDIA.md §5,
decision M3; docs/BACKUP.md §16).

``MEDIA_ROOT/originales/`` is field material that cannot be taken again. The
files are written once and never changed (app/services/media.py), so the copy
is incremental and content-addressed:

  * every run walks ``originales/`` (regular files only; symlinks and hidden
    names are skipped) and builds an index ``path -> sha256, size``. SHA-256
    values are cached by (size, mtime) in ``state/media-hashes.json``;
  * the index goes INSIDE the encrypted database bundle (``media-index.json``),
    so a restore knows which object is which file; nothing about the files is
    readable off-host;
  * every sha256 not yet verified off-host is encrypted with age to K1+K2 into
    a private staging directory, uploaded once as
    ``<prefix>media/originales/<sha256>.age`` and verified by listing it back;
    the ciphertext is then unlinked. The object name is the plaintext hash, so
    a file already present remotely is not sent again;
  * the ``media/`` prefix sits under the configured prefix (the server key is
    restricted to it) but outside the daily/weekly/monthly lifecycle rules, so
    these objects never expire; Object Lock protects them for its default
    retention;
  * the record ``state/offsite/media-originals.json`` has the same ``objects``
    shape as a backup record, so ``remote-check`` re-lists them too.

Nothing here decrypts, deletes remotely or touches the originals.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import release_common as rc  # noqa: E402  (ops/ is on sys.path; see artesa_backup)

REMOTE_DIR = "media/originales/"
INDEX_NAME = "media-index.json"
RECORD_NAME = "media-originals.json"
HASH_CACHE_NAME = "media-hashes.json"
INDEX_SCHEMA = 1


@dataclass(frozen=True)
class Original:
    path: str          # relative to originales/, POSIX
    size: int
    mtime_ns: int
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan(originals: Path, cache: dict) -> list[Original]:
    """Every regular, non-hidden file under ``originals`` (never following a
    symlink), sorted by path. ``cache`` maps path -> {size, mtime_ns, sha256}
    and is updated in place."""
    found: list[Original] = []
    seen: set[str] = set()
    for directory, dirnames, filenames in os.walk(originals, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not os.path.islink(os.path.join(directory, d)))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full = Path(directory) / name
            info = os.lstat(full)
            if not stat.S_ISREG(info.st_mode):
                continue
            rel = full.relative_to(originals).as_posix()
            cached = cache.get(rel)
            if isinstance(cached, dict) and cached.get("size") == info.st_size and cached.get("mtime_ns") == info.st_mtime_ns:
                digest = str(cached["sha256"])
            else:
                digest = _sha256(full)
            cache[rel] = {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "sha256": digest}
            seen.add(rel)
            found.append(Original(rel, info.st_size, info.st_mtime_ns, digest))
    for stale in [k for k in cache if k not in seen]:
        del cache[stale]
    return sorted(found, key=lambda o: o.path)


def index_document(originals: list[Original], created_at: str) -> dict:
    return {
        "schema_version": INDEX_SCHEMA,
        "created_at": created_at,
        "remote_dir": REMOTE_DIR,
        "files": [{"path": o.path, "size": o.size, "sha256": o.sha256} for o in originals],
        "totals": {"files": len(originals), "bytes": sum(o.size for o in originals),
                   "unique_contents": len({o.sha256 for o in originals})},
        "restore": "object <remote_dir><sha256>.age, decrypted with K1 or K2, goes to originales/<path> (docs/BACKUP.md §16)",
    }


def object_name(prefix: str, sha256: str) -> str:
    return f"{prefix}{REMOTE_DIR}{sha256}.age"


def read_record(path: Path) -> dict:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": 1, "backup_id": "media-originals", "status": "verified", "objects": []}
    if not isinstance(record, dict) or not isinstance(record.get("objects"), list):
        return {"schema_version": 1, "backup_id": "media-originals", "status": "verified", "objects": []}
    return record


def upload_missing(store, originals: list[Original], originals_dir: Path, record: dict, *,
                   encrypt: Callable[[Path, Path], None], digests: Callable[[Path], tuple[int, str, str]],
                   staging: Path, save: Callable[[dict], None], now: Callable[[], str]) -> dict:
    """Encrypt + upload + verify every content not yet in ``record``. The
    record is saved after each object, so an interrupted run resumes where it
    stopped. Raises rc.OpsError on the first failure; never deletes remotely."""
    done = {o.get("plain_sha256") for o in record["objects"]}
    uploaded = reused = 0
    for original in originals:
        if original.sha256 in done:
            continue
        name = object_name(store.cfg.prefix, original.sha256)
        existing = store.stat(name)
        sent = existing is None
        if sent:
            cipher = staging / f"{original.sha256}.age"
            try:
                encrypt(originals_dir / original.path, cipher)
                size, sha1, sha256 = digests(cipher)
                store.put(name, cipher, sha1, sha256, "application/age-encryption")
                existing = store.stat(name)
                if existing is None or existing.size != size or existing.sha1 != sha1:
                    raise rc.OpsError(rc.Exit.BACKUP, f"{name} is not listed with the uploaded size and SHA-1")
            finally:
                cipher.unlink(missing_ok=True)
            uploaded += 1
        else:
            reused += 1
        record["objects"].append({"name": name, "tier": "media", "size": existing.size, "sha1": existing.sha1,
                                  "sha256": existing.sha256, "file_id": existing.file_id, "plain_sha256": original.sha256,
                                  "uploaded": sent})
        record.update({"status": "verified", "provider": store.cfg.provider, "bucket": store.cfg.bucket, "verified_at": now()})
        done.add(original.sha256)
        save(record)
    return {"uploaded": uploaded, "reused": reused, "verified": len(done & {o.sha256 for o in originals}),
            "contents": len({o.sha256 for o in originals})}
