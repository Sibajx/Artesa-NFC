"""requirements-prod.lock: hashed, runtime-only, compiled for Python 3.14, and
in step with requirements-prod.in / requirements.txt (N-08, ADR-027)."""
import re
from pathlib import Path

import pytest

import release_common as rc

BACKEND = Path(__file__).resolve().parents[2]
LOCK = (BACKEND / "requirements-prod.lock").read_text()
IN_FILE = (BACKEND / "requirements-prod.in").read_text()
DEV = (BACKEND / "requirements.txt").read_text()
TEST_ONLY = {"pytest", "httpx"}


def direct_pins(text: str) -> dict[str, str]:
    pins = {}
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[rc.normalize_package(re.sub(r"\[.*\]", "", name))] = version.strip()
    return pins


def test_every_pin_is_hashed_and_the_lock_parses_for_python_3_14():
    pins = rc.parse_lock(LOCK, target_python="3.14")
    assert len(pins) >= 20
    assert LOCK.count("--hash=sha256:") >= len(pins)


def test_lock_is_runtime_only():
    pins = rc.parse_lock(LOCK)
    assert not (TEST_ONLY | {"pluggy", "iniconfig", "httpcore"}) & pins.keys()
    for runtime in ("fastapi", "uvicorn", "sqlalchemy", "psycopg", "psycopg-binary", "alembic", "pydantic", "pydantic-settings", "starlette"):
        assert runtime in pins


def test_direct_pins_in_the_in_file_equal_the_runtime_lines_of_requirements_txt():
    runtime_dev = {k: v for k, v in direct_pins(DEV).items() if k not in TEST_ONLY}
    assert direct_pins(IN_FILE) == runtime_dev


def test_the_lock_honours_every_direct_pin_exactly():
    lock = rc.parse_lock(LOCK)
    for name, version in direct_pins(IN_FILE).items():
        assert lock[name] == version, f"{name} drifted from requirements-prod.in"


def test_test_only_packages_stay_out_of_the_in_file():
    assert not TEST_ONLY & direct_pins(IN_FILE).keys()


def test_lock_documents_how_it_was_generated():
    assert "pip-compile" in LOCK and "--generate-hashes" in LOCK and "Python 3.14" in LOCK


# --- negative controls: the validator really rejects a bad lock -------------------------

def test_rejects_a_pin_without_hashes():
    with pytest.raises(rc.OpsError, match="without hashes"):
        rc.parse_lock(LOCK + "\nleftpad==1.0.0\n")


def test_rejects_test_only_packages():
    bad = LOCK + "\npytest==8.3.4 \\\n    --hash=sha256:" + "c" * 64 + "\n"
    with pytest.raises(rc.OpsError, match="test-only"):
        rc.parse_lock(bad)


def test_rejects_a_lock_compiled_with_another_python():
    with pytest.raises(rc.OpsError, match="not compiled with Python 3.12"):
        rc.parse_lock(LOCK, target_python="3.12")
    with pytest.raises(rc.OpsError, match="not compiled with Python 3.14"):
        rc.parse_lock(LOCK.replace("Python 3.14", "Python 3.12"), target_python="3.14")


def test_drift_is_detected_when_a_direct_pin_changes_but_the_lock_does_not():
    changed = IN_FILE.replace("sqlalchemy==2.0.44", "sqlalchemy==2.0.45")
    lock = rc.parse_lock(LOCK)
    drift = [n for n, v in direct_pins(changed).items() if lock[n] != v]
    assert drift == ["sqlalchemy"]


def test_empty_lock_is_refused():
    with pytest.raises(rc.OpsError, match="no pins"):
        rc.parse_lock("# nothing\n")
