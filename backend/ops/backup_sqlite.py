"""An extra SQLite database inside the encrypted backup (docs/BACKUP.md §17).

Finanzas (finanzas.artesanfc.com) keeps its books in one SQLite file next to
its own app. When ``shared/backup/backup.env`` names it
(``FINANZAS_DB_PATH``), every ``artesa-backup run`` takes a consistent copy
with SQLite's online backup API (the app keeps running), checks it with
``PRAGMA integrity_check`` and counts its rows, then puts it INSIDE the same
age-encrypted bundle as the PostgreSQL dump. So it inherits K1+K2, the local
retention, the off-host copy and the dead-man switch.

The source is opened read-only (``mode=ro`` URI) and never modified. A
failure here never discards the PostgreSQL backup: the run reports it and
exits non-zero, so the dead-man alarm fires.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

CONFIG_KEY = "FINANZAS_DB_PATH"
MEMBER_NAME = "finanzas/finanzas.sqlite"


class SnapshotError(Exception):
    pass


def configured_path(values: dict[str, str]) -> Path | None:
    raw = values.get(CONFIG_KEY, "").strip()
    return Path(raw) if raw else None


def snapshot(source: Path, target: Path) -> dict:
    """Consistent copy of ``source`` into ``target`` (0600). Returns the
    manifest entry: size, sha256, integrity, row counts per table."""
    if not source.is_absolute():
        raise SnapshotError(f"{CONFIG_KEY} must be an absolute path")
    try:
        info = os.lstat(source)
    except FileNotFoundError:
        raise SnapshotError(f"{CONFIG_KEY} does not exist") from None
    if not stat.S_ISREG(info.st_mode):
        raise SnapshotError(f"{CONFIG_KEY} is not a regular file")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        src = sqlite3.connect(f"file:{quote(str(source))}?mode=ro", uri=True, timeout=30)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        check = sqlite3.connect(f"file:{quote(str(target))}?mode=ro", uri=True)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            tables = [r[0] for r in check.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            counts = {t: check.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        finally:
            check.close()
    except sqlite3.Error as exc:
        raise SnapshotError(f"SQLite copy failed ({type(exc).__name__})") from None
    if integrity != "ok":
        raise SnapshotError("the copy failed PRAGMA integrity_check")
    os.chmod(target, 0o600)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {"member": MEMBER_NAME, "size": os.stat(target).st_size, "sha256": digest, "integrity": "ok",
            "table_counts": counts}
