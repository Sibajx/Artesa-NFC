"""Shared helpers for the credential-safety tests (test_config.py,
test_db_safety.py, test_seed_safety.py)."""
from __future__ import annotations

import traceback

# Distinctive values so a leak into any error text is unmistakable.
CANARY_USER = "LeakCanaryUser"
CANARY_PASSWORD = "LeakCanaryPW"


def credentialed_url(database: str, host: str = "localhost") -> str:
    return f"postgresql://{CANARY_USER}:{CANARY_PASSWORD}@{host}:5432/{database}"


def assert_no_secrets(exc: BaseException, *forbidden_in_message: str) -> None:
    """The user/password canaries must appear nowhere -- message, repr or the
    full formatted traceback (chained causes included). Full URLs and any
    ``forbidden_in_message`` text (e.g. a host name) must not appear in the
    message or repr; the rendered traceback is exempt from that second check
    only because it echoes the *test's own* source lines."""
    messages = (str(exc), repr(exc))
    rendered = "".join(traceback.format_exception(exc))
    for secret in (CANARY_USER, CANARY_PASSWORD):
        for text in (*messages, rendered):
            assert secret not in text
    for text in messages:
        assert "postgresql://" not in text
        for extra in forbidden_in_message:
            assert extra not in text
