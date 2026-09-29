"""Admin (Gestión, ADR-029) settings: all three keys or none."""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.db_safety import UnsafeConfigurationError

LOCAL_DB = "postgresql://example:example@localhost:5432/example"
FULL = {
    "admin_access_team_domain": "artesanfc.cloudflareaccess.com",
    "admin_access_aud": "ab" * 32,
    "admin_emails": "Ops@Example.org, second@example.org",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for key in ("APP_ENV", "DATABASE_URL", "CORS_ALLOWED_ORIGINS", "DEBUG",
                "ADMIN_ACCESS_TEAM_DOMAIN", "ADMIN_ACCESS_AUD", "ADMIN_EMAILS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", LOCAL_DB)


def test_admin_is_disabled_by_default():
    settings = Settings(_env_file=None)
    assert settings.admin_enabled is False
    assert settings.admin_emails_list == []


def test_full_admin_configuration_is_enabled_and_normalized():
    settings = Settings(_env_file=None, **{**FULL, "admin_access_team_domain": " ArtesaNFC.cloudflareaccess.com "})
    assert settings.admin_enabled is True
    assert settings.admin_access_team_domain == "artesanfc.cloudflareaccess.com"
    assert settings.admin_emails_list == ["ops@example.org", "second@example.org"]


@pytest.mark.parametrize("missing", sorted(FULL))
def test_partial_admin_configuration_refuses_to_start(missing):
    with pytest.raises(UnsafeConfigurationError, match="partial admin configuration"):
        Settings(_env_file=None, **{k: v for k, v in FULL.items() if k != missing})


@pytest.mark.parametrize("field,value", [
    ("admin_access_team_domain", "https://artesanfc.cloudflareaccess.com"),
    ("admin_access_team_domain", "artesanfc.cloudflareaccess.com/cdn-cgi"),
    ("admin_access_team_domain", "artesanfc.example.com"),
    ("admin_access_aud", "not-hex"),
    ("admin_access_aud", "ab" * 31),
    ("admin_emails", "not-an-email"),
])
def test_malformed_admin_values_refuse_to_start(field, value):
    with pytest.raises(UnsafeConfigurationError):
        Settings(_env_file=None, **{**FULL, field: value})


def test_admin_configuration_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("ADMIN_ACCESS_TEAM_DOMAIN", FULL["admin_access_team_domain"])
    monkeypatch.setenv("ADMIN_ACCESS_AUD", FULL["admin_access_aud"])
    monkeypatch.setenv("ADMIN_EMAILS", FULL["admin_emails"])
    assert Settings(_env_file=None).admin_enabled is True
