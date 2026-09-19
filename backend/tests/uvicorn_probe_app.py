"""Test-only ASGI app served by a REAL Uvicorn process in
tests/test_global_db_error_uvicorn.py. Never imported by the application or by
the pytest process itself (it would add routes to the shared `app`); it is only
ever loaded by the subprocess: ``python -m uvicorn tests.uvicorn_probe_app:app``.

It is the real `app.main:app` (real middleware, real exception handlers, real
routes such as POST /api/v1/certificates/resolve) plus a few /probe routes that
raise genuine PostgreSQL failures through tests/db_failure_helpers.py. Canary
values arrive through the environment so each test run uses fresh ones.
"""
from __future__ import annotations

import os

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.db_safety import assert_safe_for_tests
from app.main import app
from tests import db_failure_helpers as failures

# Same guard as the pytest session: this process writes nothing, but it must
# still never be pointed at anything but a marked test database.
_settings = get_settings()
assert_safe_for_tests(_settings.app_env, _settings.database_url)

CANARY_HASH = os.environ["PROBE_CANARY_HASH"]
CANARY_SQL = os.environ["PROBE_CANARY_SQL"]
CANARY_RUNTIME = os.environ["PROBE_CANARY_RUNTIME"]


@app.get("/probe/integrity")
def integrity(db: Session = Depends(get_db)):
    failures.insert_duplicate_token_hash(db, CANARY_HASH)


@app.get("/probe/pending-rollback")
def pending_rollback(db: Session = Depends(get_db)):
    failures.pending_rollback_after_swallowed_failure(db, CANARY_HASH)


@app.get("/probe/operational")
def operational(db: Session = Depends(get_db)):
    failures.statement_timeout_failure(db, CANARY_SQL)


@app.get("/probe/runtime")
def runtime():
    raise RuntimeError(f"non-database programmer error {CANARY_RUNTIME}")
