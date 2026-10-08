"""Transactional email settings (Brevo SMTP) and the sender: all four or none."""
from __future__ import annotations

import smtplib

import pytest

from app.core.config import Settings
from app.core.db_safety import UnsafeConfigurationError
from app.services import mailer

LOCAL_DB = "postgresql://example:example@localhost:5432/example"
FULL = {"smtp_host": "smtp-relay.brevo.com", "smtp_user": "u@smtp-brevo.com", "smtp_password": "s3cret",
        "mail_from": "ArtesaNFC <no-reply@artesanfc.com>"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key in ("APP_ENV", "DATABASE_URL", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", LOCAL_DB)


def test_mail_is_off_by_default():
    assert Settings(_env_file=None).mail_configured is False


def test_full_mail_configuration_is_on_and_hides_the_password():
    settings = Settings(_env_file=None, **FULL)
    assert settings.mail_configured is True and settings.smtp_port == 587
    assert "s3cret" not in repr(settings)


@pytest.mark.parametrize("missing", sorted(FULL))
def test_partial_mail_configuration_refuses_to_start(missing):
    partial = {k: v for k, v in FULL.items() if k != missing}
    with pytest.raises(UnsafeConfigurationError, match="partial mail configuration"):
        Settings(_env_file=None, **partial)


def test_send_without_settings_raises_unavailable(monkeypatch):
    monkeypatch.setattr(mailer, "get_settings", lambda: Settings(_env_file=None))
    with pytest.raises(mailer.MailUnavailable):
        mailer.send_email("a@example.com", "s", "b")


def test_send_uses_starttls_login_and_hides_relay_errors(monkeypatch):
    monkeypatch.setattr(mailer, "get_settings", lambda: Settings(_env_file=None, **FULL))
    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(f"connect {host}:{port} {timeout}")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context):
            calls.append("starttls")

        def login(self, user, password):
            calls.append(f"login {user}")

        def send_message(self, message):
            calls.append(f"send {message['To']} {message['From']}")

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    mailer.send_email("owner@example.com", "Hola", "cuerpo")
    assert calls == ["connect smtp-relay.brevo.com:587 10", "starttls", "login u@smtp-brevo.com",
                     "send owner@example.com ArtesaNFC <no-reply@artesanfc.com>"]

    def boom(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials s3cret")

    monkeypatch.setattr(mailer.smtplib, "SMTP", boom)
    with pytest.raises(mailer.MailError) as caught:
        mailer.send_email("owner@example.com", "Hola", "cuerpo")
    assert "s3cret" not in str(caught.value)
