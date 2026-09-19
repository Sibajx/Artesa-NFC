"""docker-compose.yml must not reintroduce an implicit DATABASE_URL (issue #101).

The `api` service gets DATABASE_URL only from `.env` (env_file). A compose-level
fallback such as ``${DATABASE_URL:-postgresql://...}`` would inject a database
into the container even when .env has none, bypassing Settings' fail-closed
check. Static text checks only: no Docker daemon is needed. The real behaviour
(container refuses to start without DATABASE_URL) is validated by hand, see
backend/README.md "Docker scope".
"""
from __future__ import annotations

import re

from app.core.config import BACKEND_DIR

COMPOSE_TEXT = (BACKEND_DIR / "docker-compose.yml").read_text(encoding="utf-8")


def test_compose_has_no_database_url_fallback():
    # ${DATABASE_URL:-x} and ${DATABASE_URL-x}
    assert not re.search(r"\$\{DATABASE_URL:?-", COMPOSE_TEXT)


def test_compose_does_not_set_database_url_directly():
    assert not re.search(r"^\s*DATABASE_URL\s*:", COMPOSE_TEXT, re.MULTILINE)
    assert "postgresql://" not in COMPOSE_TEXT


def test_compose_api_service_reads_its_configuration_from_env_file():
    api_block = COMPOSE_TEXT.split("\n  api:", 1)[1].split("\nvolumes:", 1)[0]
    assert re.search(r"env_file:\s*\n(?:\s*#.*\n)*\s*-\s*\.env\b", api_block)


def test_compose_does_not_use_strict_required_interpolation_for_database_url():
    # ${DATABASE_URL:?...} is evaluated for the whole file, so it would also
    # make `docker compose up db` fail when DATABASE_URL is unset.
    assert not re.search(r"^[^#\n]*\$\{DATABASE_URL:?\?", COMPOSE_TEXT, re.MULTILINE)
