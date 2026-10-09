"""Transactional email over SMTP (Brevo relay), standard library only.

Used for the one-time codes that confirm the owner of a piece. The message
never carries the card key or the PIN, only the code. Nothing here logs the
recipient, the code or the credentials.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings

SMTP_TIMEOUT_SECONDS = 10


class MailUnavailable(Exception):
    """No SMTP settings: nothing can be sent."""


class MailError(Exception):
    """The relay refused or could not be reached."""


def send_email(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.mail_configured:
        raise MailUnavailable()
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(type(exc).__name__) from None
