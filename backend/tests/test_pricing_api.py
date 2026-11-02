"""Calculadora de precio al público: rutas de Gestión, permisos y auditoría."""
from __future__ import annotations

from sqlalchemy import select

from app.models.audit_event import AuditEvent
from app.services.pricing import SETTINGS_UUID
from tests.test_accounts_and_authorization import FIXED, OWNER, H, owner_client  # noqa: F401  (fixture)
from tests.test_admin_api import auth, make_token

SETTINGS = "/api/admin/v1/pricing/settings"
QUOTE = "/api/admin/v1/pricing/quote"


def test_defaults_before_anyone_saves(owner_client):
    r = owner_client.get(SETTINGS, headers=auth(make_token(email=FIXED)))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated_at"] is None
    assert body["config"]["mode"] == "intermediario"
    assert body["config"]["round_to_cents"] == 10000


def test_quote_uses_the_saved_settings(owner_client):
    r = owner_client.post(QUOTE, json={"artisan_price_cents": 600000, "commission_pct": 20},
                          headers=auth(make_token(email=FIXED)))
    assert r.status_code == 200, r.text
    assert r.json()["public_price_cents"] == 780000
    cfg = owner_client.get(SETTINGS, headers=auth(make_token(email=OWNER))).json()["config"]
    saved = owner_client.put(SETTINGS, json={**cfg, "mode": "revendedor"}, headers=H())
    assert saved.status_code == 200, saved.text
    assert saved.json()["updated_by"] == OWNER
    r = owner_client.post(QUOTE, json={"artisan_price_cents": 600000, "commission_pct": 20},
                          headers=auth(make_token(email=FIXED)))
    assert r.json()["public_price_cents"] == 880000
    # Without a commission, the tier for the amount.
    tier = owner_client.post(QUOTE, json={"artisan_price_cents": 250000}, headers=auth(make_token(email=FIXED)))
    assert float(tier.json()["commission_pct"]) == 25


def test_only_the_owner_changes_the_settings_and_it_is_audited(owner_client, db_session):
    cfg = owner_client.get(SETTINGS, headers=auth(make_token(email=OWNER))).json()["config"]
    assert owner_client.put(SETTINGS, json=cfg, headers=H(email=FIXED)).status_code == 403
    bad = owner_client.put(SETTINGS, json={**cfg, "iva_pct": 120}, headers=H())
    assert bad.status_code == 422
    ok = owner_client.put(SETTINGS, json={**cfg, "isr_pct": "2.5"}, headers=H())
    assert ok.status_code == 200 and float(ok.json()["config"]["isr_pct"]) == 2.5
    events = db_session.execute(select(AuditEvent).where(AuditEvent.entity_id == SETTINGS_UUID)).scalars().all()
    assert [e.action for e in events] == ["pricing.settings_updated"]
    assert float(events[0].event_metadata["after"]["isr_pct"]) == 2.5


def test_writes_need_the_admin_header(owner_client):
    cfg = owner_client.get(SETTINGS, headers=auth(make_token(email=OWNER))).json()["config"]
    assert owner_client.put(SETTINGS, json=cfg, headers=auth(make_token(email=OWNER))).status_code in (400, 403)


def test_quote_rejects_nonsense(owner_client):
    h = auth(make_token(email=FIXED))
    assert owner_client.post(QUOTE, json={"artisan_price_cents": -1}, headers=h).status_code == 422
    assert owner_client.post(QUOTE, json={"artisan_price_cents": 100, "commission_pct": 101}, headers=h).status_code == 422
    assert owner_client.post(QUOTE, json={"artisan_price_cents": 100}).status_code in (401, 403)
