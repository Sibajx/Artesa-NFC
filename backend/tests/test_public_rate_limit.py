"""PEND-005 / PEND-025: per-IP limit on the public GETs and the abuse cooldown
(app/core/rate_limit.py, docs/SECURITY.md section 5.2)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.rate_limit import PublicRateLimiter
from app.main import app

client = TestClient(app)
KW = {"limit": 3, "abuse_rejections": 2, "cooldown": 100.0}


# --- the limiter -----------------------------------------------------------------


def test_allows_up_to_the_limit_then_refuses_with_the_wait():
    lim = PublicRateLimiter()
    assert [lim.check("1.1.1.1", t, **KW) for t in (0.0, 1.0, 2.0)] == [None, None, None]
    wait = lim.check("1.1.1.1", 3.0, **KW)
    assert wait == pytest.approx(57.0)  # the oldest hit leaves the window at t=60


def test_window_slides_and_other_ips_are_independent():
    lim = PublicRateLimiter()
    for t in (0.0, 1.0, 2.0):
        lim.check("1.1.1.1", t, **KW)
    assert lim.check("2.2.2.2", 3.0, **KW) is None
    assert lim.check("1.1.1.1", 60.5, **KW) is None  # the hit at t=0 expired


def test_sustained_abuse_triggers_the_cooldown():
    lim = PublicRateLimiter()
    for t in (0.0, 1.0, 2.0):
        lim.check("1.1.1.1", t, **KW)
    assert lim.check("1.1.1.1", 3.0, **KW) is not None  # first rejection
    assert lim.check("1.1.1.1", 4.0, **KW) == 100.0  # second: cooldown starts
    # inside the cooldown everything is refused, even once the window cleared
    assert lim.check("1.1.1.1", 90.0, **KW) == pytest.approx(14.0)
    assert lim.check("1.1.1.1", 104.5, **KW) is None  # over


def test_a_polite_client_never_hits_the_cooldown():
    lim = PublicRateLimiter()
    # one refused request now and then does not accumulate into a cooldown
    for base in (0.0, 200.0, 400.0):
        for t in (0.0, 1.0, 2.0):
            assert lim.check("1.1.1.1", base + t, **KW) is None
        assert lim.check("1.1.1.1", base + 3.0, **KW) is not None


# --- the middleware ----------------------------------------------------------------


def test_public_get_is_limited_with_envelope_and_retry_after(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_rate_limit_per_minute", 2)
    assert client.get("/api/v1/artisans").status_code == 200
    assert client.get("/api/v1/artisans").status_code == 200
    r = client.get("/api/v1/artisans")
    assert r.status_code == 429
    assert r.json() == {"error": {"code": "rate_limited", "message": "Too many requests."}}
    assert 1 <= int(r.headers["Retry-After"]) <= 60
    assert r.headers["Cache-Control"] == "no-store"


def test_out_of_scope_requests_are_not_counted(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_rate_limit_per_minute", 1)
    for _ in range(5):
        assert client.get("/health").status_code in (200, 503)
        assert client.options("/api/v1/artisans", headers={"Origin": "https://artesanfc.com"}).status_code != 429
        assert client.get("/api/admin/v1/does-not-exist").status_code != 429
    assert client.get("/api/v1/artisans").status_code == 200


def test_zero_turns_the_limit_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_rate_limit_per_minute", 0)
    for _ in range(10):
        assert client.get("/api/v1/artisans").status_code == 200


def test_cooldown_through_the_app(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "public_rate_limit_per_minute", 1)
    monkeypatch.setattr(settings, "public_abuse_rejections", 2)
    statuses = [client.get("/api/v1/artisans").status_code for _ in range(4)]
    assert statuses == [200, 429, 429, 429]
    r = client.get("/api/v1/artisans")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 60  # the cooldown, not the window
