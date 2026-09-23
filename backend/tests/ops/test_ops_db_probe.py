"""release_probe.py db-state against the real (test) PostgreSQL, as a
subprocess exactly the way the deploy tool runs it inside a release venv."""
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

OPS = Path(__file__).resolve().parents[2] / "ops" / "release_probe.py"


def run_probe(env_extra=None):
    env = {"PATH": os.environ.get("PATH", ""), "DATABASE_URL": os.environ["DATABASE_URL"], "PYTHONDONTWRITEBYTECODE": "1", **(env_extra or {})}
    return subprocess.run([sys.executable, str(OPS), "db-state"], env=env, capture_output=True, text=True, timeout=60)


def test_db_state_reports_revision_tables_and_counts_and_writes_nothing():
    before = _fingerprint()
    result = run_probe()
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["alembic_versions"] == ["895974720462"]
    assert {"alembic_version", "artisan", "piece", "certificate", "nfc_tag", "media_asset"} <= set(state["row_counts"])
    assert state["server_version_num"] >= 160000
    assert _fingerprint() == before


def test_db_state_runs_in_a_read_only_transaction():
    """A write attempted inside the probe's transaction mode must fail: prove
    the mode by asking the server what the probe would have used."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cur = conn.cursor()
        cur.execute("SET TRANSACTION READ ONLY")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            cur.execute("CREATE TEMP TABLE probe_must_not_write (x int)")
        conn.rollback()
    source = OPS.read_text()
    assert 'SET TRANSACTION READ ONLY' in source and "conn.rollback()" in source
    assert not any(word in source.upper() for word in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "ALTER ", "TRUNCATE"))


def test_db_state_failure_prints_only_the_exception_class_and_no_url():
    result = run_probe({"DATABASE_URL": "postgresql://nobody:LeakMePassword99@127.0.0.1:1/none"})
    assert result.returncode == 23
    assert "LeakMePassword99" not in result.stdout + result.stderr and "127.0.0.1" not in result.stdout + result.stderr
    assert "database probe failed (" in result.stderr


def test_db_state_without_database_url_is_a_configuration_error():
    result = subprocess.run([sys.executable, str(OPS), "db-state"], env={"PATH": os.environ.get("PATH", "")}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 20 and "DATABASE_URL" in result.stderr


def test_db_state_rejects_other_arguments():
    assert subprocess.run([sys.executable, str(OPS), "wipe"], capture_output=True, text=True).returncode == 2


def _fingerprint():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cur = conn.cursor()
        cur.execute("SELECT relname, n_tup_ins, n_tup_upd, n_tup_del FROM pg_stat_user_tables ORDER BY 1")
        stats = cur.fetchall()
        cur.execute("SELECT version_num FROM alembic_version")
        return stats, cur.fetchall()
