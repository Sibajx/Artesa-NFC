"""docs/BACKUP.md §17: the Finanzas SQLite inside the encrypted backup (B-032)."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

import backup_sqlite as bsq
import release_common as rc
from tests.ops.test_backup_d10 import backups, layout, plaintext_anywhere, s, state  # noqa: F401 -- fixture reuse
from tests.ops.test_backup_d10_2 import run
from tests.ops.test_backup_media import bundle_members


def make_finanzas(path: Path, rows: int = 3) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, concepto TEXT, monto REAL)")
    conn.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, proveedor TEXT)")
    conn.executemany("INSERT INTO transactions (concepto, monto) VALUES (?, ?)",
                     [(f"Movimiento secreto {i}", 100.0 + i) for i in range(rows)])
    conn.execute("INSERT INTO invoices (proveedor) VALUES ('Pemex')")
    conn.commit()
    conn.close()
    return path


def configure(s, db_path: Path | str) -> None:
    config = s.root / "shared" / "backup" / "backup.env"
    text = config.read_text()
    config.write_text(text + f"{bsq.CONFIG_KEY}={db_path}\n")
    os.chmod(config, 0o600)


def test_without_the_option_nothing_changes(s):
    assert run(s, "run") == 0
    assert "FINANZAS: NOT CONFIGURED" in s.sink.text
    members = bundle_members(backups(s)[0])
    assert bsq.MEMBER_NAME not in members
    assert json.loads(members["manifest.json"])["finanzas"] == {"status": "not-configured"}


def test_a_consistent_copy_goes_inside_the_encrypted_bundle(s, tmp_path):
    source = make_finanzas(tmp_path / "artesa_finanzas.db")
    before = source.read_bytes()
    configure(s, source)
    assert run(s, "run") == 0
    assert "FINANZAS: included in the encrypted bundle" in s.sink.text and "integrity ok, 4 rows" in s.sink.text
    members = bundle_members(backups(s)[0])
    copy = tmp_path / "restored.sqlite"
    copy.write_bytes(members[bsq.MEMBER_NAME])
    conn = sqlite3.connect(copy)
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 3
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    manifest = json.loads(members["manifest.json"])["finanzas"]
    assert manifest["status"] == "included" and manifest["table_counts"] == {"invoices": 1, "transactions": 3}
    assert state(s)["finanzas"]["status"] == "included"
    # The source is never modified, and nothing readable is left outside the encrypted bundle.
    assert source.read_bytes() == before
    assert not plaintext_anywhere(s)
    for path in layout(s).base.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert not data.startswith(b"SQLite format 3") and b"Movimiento secreto" not in data, path
    assert b"Movimiento secreto" not in (backups(s)[0] / "meta.json").read_bytes()


def test_a_broken_finanzas_keeps_the_artesanfc_backup_and_fails_the_run(s, tmp_path):
    configure(s, tmp_path / "no-existe.db")
    code = run(s, "run")
    assert code == int(rc.Exit.BACKUP)
    assert "FINANZAS: FAILED" in s.sink.text and "does not exist" in s.sink.text
    members = bundle_members(backups(s)[0])  # the PostgreSQL backup is there
    assert "database.dump" in members and bsq.MEMBER_NAME not in members
    assert state(s)["finanzas"]["status"] == "failed"
    assert run(s, "status") != 0  # status is not healthy until the next good run


def test_a_corrupt_file_is_refused(s, tmp_path):
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is not a sqlite database" * 100)
    configure(s, bad)
    assert run(s, "run") == int(rc.Exit.BACKUP)
    assert state(s)["finanzas"]["status"] == "failed"


def test_snapshot_rejects_relative_paths_and_symlinks(tmp_path):
    source = make_finanzas(tmp_path / "f.db")
    with pytest.raises(bsq.SnapshotError, match="absolute"):
        bsq.snapshot(Path("f.db"), tmp_path / "out1")
    link = tmp_path / "link.db"
    link.symlink_to(source)
    with pytest.raises(bsq.SnapshotError, match="regular file"):
        bsq.snapshot(link, tmp_path / "out2")
    info = bsq.snapshot(source, tmp_path / "out3")
    assert info["integrity"] == "ok" and oct((tmp_path / "out3").stat().st_mode & 0o777) == "0o600"
