"""P-028: the home hero by season (Gestión → Hero)."""
from __future__ import annotations

import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_db
from app.core.access import AccessVerifier, get_access_verifier
from app.core.config import get_settings
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.hero_campaign import HeroCampaign
from app.services import hero as hero_service
from tests.test_admin_api import AUD, CUSTODIAN_EMAIL, TEAM, FakeJWKS, auth, make_token

OWNER = "owner@example.org"
SOL = "sol@example.org"
EDITOR = "ops@example.org"

FFMPEG = hero_service.ffmpeg_available()
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")


@pytest.fixture()
def hero_client(db_session, monkeypatch, tmp_path):
    v = AccessVerifier(TEAM, AUD, (EDITOR, CUSTODIAN_EMAIL, OWNER), jwks_client=FakeJWKS(),
                       custodians=(CUSTODIAN_EMAIL,), owners=(OWNER,))
    app.dependency_overrides[get_access_verifier] = lambda: v

    def _db():
        yield db_session

    app.dependency_overrides[get_db] = _db
    s = get_settings()
    monkeypatch.setattr(s, "admin_emails", f"{EDITOR},{CUSTODIAN_EMAIL},{OWNER}")
    monkeypatch.setattr(s, "owner_emails", OWNER)
    monkeypatch.setattr(s, "custodian_emails", CUSTODIAN_EMAIL)
    root = tmp_path / "media"
    (root / "originales").mkdir(parents=True)
    (root / "publico").mkdir()
    monkeypatch.setattr(s, "media_root", str(root))
    yield TestClient(app)
    app.dependency_overrides.pop(get_access_verifier, None)
    app.dependency_overrides.pop(get_db, None)


def H(email=OWNER, **extra):
    return {**auth(make_token(email=email)), "X-Artesa-Admin": "1", **extra}


def JSON(email=OWNER):
    return {**H(email), "Content-Type": "application/json"}


def state(c, email=OWNER):
    r = c.get("/api/admin/v1/hero", headers=auth(make_token(email=email)))
    assert r.status_code == 200, r.text
    return r.json()


def by_slug(body, slug):
    return next(c for c in body["campaigns"] if c["slug"] == slug)


def sample_video(path: Path, seconds=20, size="1280x720", audio=True) -> bytes:
    args = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            f"testsrc=duration={seconds}:size={size}:rate=15"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True)
    return path.read_bytes()


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "default=nw=1", str(path)], capture_output=True, check=True).stdout.decode()
    return dict(line.split("=", 1) for line in out.split())


def campaign(db, slug) -> HeroCampaign:
    return db.execute(select(HeroCampaign).where(HeroCampaign.slug == slug)).scalar_one()


def make_ready(db, slug, **extra):
    c = campaign(db, slug)
    c.processing_status, c.video_mp4, c.video_webm, c.poster = (
        "ready", f"hero/{slug}/aaaaaaaaaaaa.mp4", f"hero/{slug}/aaaaaaaaaaaa.webm", f"hero/{slug}/aaaaaaaaaaaa.jpg")
    for k, v in extra.items():
        setattr(c, k, v)
    db.flush()
    return c


# --- which campaign shows ---------------------------------------------------------------


def test_the_seeded_campaigns_and_their_dates(db_session):
    names = {c.slug: c for c in hero_service.all_campaigns(db_session)}
    assert {"hero-normal", "dia-de-muertos", "navidad"} <= set(names)
    assert names["hero-normal"].is_default and names["hero-normal"].start_month is None
    muertos = names["dia-de-muertos"]
    assert hero_service.covers(muertos, date(2026, 10, 20)) and hero_service.covers(muertos, date(2026, 11, 2))
    assert not hero_service.covers(muertos, date(2026, 10, 19)) and not hero_service.covers(muertos, date(2026, 11, 3))
    assert not hero_service.covers(names["hero-normal"], date(2026, 10, 25))


def test_a_range_that_wraps_the_year(db_session):
    c = HeroCampaign(slug="x", name="X", is_default=False, start_month=12, start_day=28, end_month=1, end_day=3)
    assert all(hero_service.covers(c, d) for d in (date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 3)))
    assert not hero_service.covers(c, date(2027, 1, 4)) and not hero_service.covers(c, date(2026, 12, 27))


def test_nothing_shows_until_a_video_is_ready_and_published(db_session):
    today = date(2026, 10, 25)
    assert hero_service.current(db_session, today) == (None, None)
    c = make_ready(db_session, "dia-de-muertos")
    assert hero_service.current(db_session, today) == (None, None)  # ready, not published
    c.published = True
    assert hero_service.current(db_session, today) == (c, "date")
    assert hero_service.current(db_session, date(2026, 11, 3)) == (None, None)  # out of range, no default


def test_the_default_is_the_fallback_and_a_dated_campaign_wins(db_session):
    normal = make_ready(db_session, "hero-normal", published=True)
    assert hero_service.current(db_session, date(2026, 3, 1)) == (normal, "default")
    muertos = make_ready(db_session, "dia-de-muertos", published=True)
    assert hero_service.current(db_session, date(2026, 10, 31)) == (muertos, "date")


def test_forced_wins_until_its_end_date(db_session):
    muertos = make_ready(db_session, "dia-de-muertos", published=True)
    navidad = make_ready(db_session, "navidad", forced=True, forced_until=date(2026, 10, 26))
    assert hero_service.current(db_session, date(2026, 10, 25)) == (navidad, "forced")
    assert hero_service.current(db_session, date(2026, 10, 27)) == (muertos, "date")


def test_the_latest_start_wins_when_ranges_overlap(db_session):
    a = make_ready(db_session, "dia-de-muertos", published=True)
    b = HeroCampaign(slug="altar", name="Altar", is_default=False, start_month=10, start_day=28, end_month=11,
                     end_day=2, published=True, processing_status="ready", video_mp4="hero/altar/b.mp4",
                     poster="hero/altar/b.jpg")
    db_session.add(b)
    db_session.flush()
    assert hero_service.current(db_session, date(2026, 10, 30))[0] == b
    assert hero_service.current(db_session, date(2026, 10, 22))[0] == a


def test_a_stuck_job_is_reported_as_an_error(db_session):
    c = campaign(db_session, "navidad")
    c.processing_status = "processing"
    c.processing_started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    assert hero_service.status_of(c, None) == "error" and "interrumpió" in hero_service.error_of(c)
    c.processing_started_at = datetime.now(timezone.utc)
    assert hero_service.status_of(c, None) == "processing" and hero_service.error_of(c) is None


# --- access ------------------------------------------------------------------------------


def test_only_hero_accounts_and_the_owner_reach_the_page(hero_client):
    assert hero_client.get("/api/admin/v1/hero", headers=auth(make_token(email=EDITOR))).status_code == 403
    assert hero_client.get("/api/admin/v1/hero", headers=auth(make_token(email=CUSTODIAN_EMAIL))).status_code == 403
    assert state(hero_client)["campaigns"]  # the owner
    me = hero_client.get("/api/admin/v1/me", headers=auth(make_token(email=OWNER))).json()
    assert "hero" in me["roles"]


def test_the_partner_gets_the_hero_role_from_usuarios(hero_client):
    assert hero_client.get("/api/admin/v1/hero", headers=auth(make_token(email=SOL))).status_code == 403
    r = hero_client.post("/api/admin/v1/accounts", json={"email": SOL, "role": "hero"}, headers=JSON())
    assert r.status_code == 200, r.text
    assert state(hero_client, SOL)["today"]
    me = hero_client.get("/api/admin/v1/me", headers=auth(make_token(email=SOL))).json()
    assert "hero" in me["roles"] and "owner" not in me["roles"] and "custodian" not in me["roles"]
    assert hero_client.get("/api/admin/v1/accounts", headers=auth(make_token(email=SOL))).status_code == 403


def test_writes_need_the_admin_header(hero_client):
    r = hero_client.post("/api/admin/v1/hero/force", json={"campaign_id": "00000000-0000-0000-0000-000000000000"},
                         headers={**auth(make_token(email=OWNER)), "Content-Type": "application/json"})
    assert r.status_code == 403


# --- campaigns -----------------------------------------------------------------------------


def test_create_edit_and_delete_a_campaign(hero_client, db_session):
    r = hero_client.post("/api/admin/v1/hero/campaigns", headers=JSON(),
                         json={"name": "Guelaguetza", "start_month": 7, "start_day": 15, "end_month": 7, "end_day": 29})
    assert r.status_code == 201, r.text
    g = by_slug(r.json(), "guelaguetza")
    assert g["status"] == "no_video" and not g["published"]
    r = hero_client.patch(f"/api/admin/v1/hero/campaigns/{g['id']}", headers=JSON(), json={"name": "Guelaguetza 2"})
    assert by_slug(r.json(), "guelaguetza")["name"] == "Guelaguetza 2"
    r = hero_client.patch(f"/api/admin/v1/hero/campaigns/{g['id']}", headers=JSON(),
                          json={"start_month": 2, "start_day": 30, "end_month": 3, "end_day": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_date"
    r = hero_client.patch(f"/api/admin/v1/hero/campaigns/{g['id']}", headers=JSON(), json={"start_month": 2})
    assert r.status_code == 422
    r = hero_client.delete(f"/api/admin/v1/hero/campaigns/{g['id']}", headers=JSON())
    assert r.status_code == 200 and not any(c["slug"] == "guelaguetza" for c in r.json()["campaigns"])
    actions = {e.action for e in db_session.execute(select(AuditEvent).where(AuditEvent.entity_type == "hero_campaign")).scalars()}
    assert {"hero.created", "hero.updated", "hero.deleted"} <= actions


def test_the_default_cannot_be_deleted_or_given_dates(hero_client):
    normal = by_slug(state(hero_client), "hero-normal")
    r = hero_client.delete(f"/api/admin/v1/hero/campaigns/{normal['id']}", headers=JSON())
    assert r.status_code == 409 and r.json()["error"]["code"] == "default_campaign"
    r = hero_client.patch(f"/api/admin/v1/hero/campaigns/{normal['id']}", headers=JSON(),
                          json={"start_month": 1, "start_day": 1, "end_month": 1, "end_day": 2})
    assert r.status_code == 422


def test_publish_and_force_need_a_ready_video(hero_client):
    muertos = by_slug(state(hero_client), "dia-de-muertos")
    r = hero_client.post(f"/api/admin/v1/hero/campaigns/{muertos['id']}/publish", headers=JSON(), json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_video"
    r = hero_client.post("/api/admin/v1/hero/force", headers=JSON(), json={"campaign_id": muertos["id"]})
    assert r.status_code == 409


def test_publish_force_and_unforce(hero_client, db_session):
    muertos = make_ready(db_session, "dia-de-muertos")
    navidad = make_ready(db_session, "navidad")
    r = hero_client.post(f"/api/admin/v1/hero/campaigns/{muertos.id}/publish", headers=JSON(), json={})
    assert by_slug(r.json(), "dia-de-muertos")["published"]
    assert by_slug(r.json(), "dia-de-muertos")["status"] in ("live", "scheduled")
    r = hero_client.post("/api/admin/v1/hero/force", headers=JSON(),
                         json={"campaign_id": str(navidad.id), "until": "2099-01-01"})
    body = r.json()
    assert r.status_code == 200 and body["live_id"] == str(navidad.id) and body["live_reason"] == "forced"
    assert by_slug(body, "navidad")["forced"] and by_slug(body, "navidad")["status"] == "live"
    # Forcing another one replaces it.
    r = hero_client.post("/api/admin/v1/hero/force", headers=JSON(), json={"campaign_id": str(muertos.id)})
    assert [c["slug"] for c in r.json()["campaigns"] if c["forced"]] == ["dia-de-muertos"]
    assert hero_client.post("/api/admin/v1/hero/force", headers=JSON(),
                            json={"campaign_id": str(muertos.id), "until": "2020-01-01"}).status_code == 422
    r = hero_client.delete("/api/admin/v1/hero/force", headers=JSON())
    assert not any(c["forced"] for c in r.json()["campaigns"])
    r = hero_client.post(f"/api/admin/v1/hero/campaigns/{muertos.id}/unpublish", headers=JSON(), json={})
    assert not by_slug(r.json(), "dia-de-muertos")["published"]


def test_the_public_endpoint(hero_client, db_session):
    r = hero_client.get("/api/v1/hero")
    assert r.status_code == 200 and r.json() == {"data": None}
    assert r.headers["cache-control"] == "public, max-age=300"
    make_ready(db_session, "hero-normal", published=True)
    body = hero_client.get("/api/v1/hero").json()["data"]
    assert body["slug"] == "hero-normal" and body["reason"] == "default"
    assert body["video"] == {"mp4": "/media/hero/hero-normal/aaaaaaaaaaaa.mp4",
                             "webm": "/media/hero/hero-normal/aaaaaaaaaaaa.webm"}
    assert body["poster"] == "/media/hero/hero-normal/aaaaaaaaaaaa.jpg"


# --- the video -----------------------------------------------------------------------------


def upload(c, campaign_id, data, email=OWNER, content_type="video/mp4"):
    return c.post(f"/api/admin/v1/hero/campaigns/{campaign_id}/video", content=data,
                  headers={**H(email), "Content-Type": content_type})


@needs_ffmpeg
def test_a_video_is_cropped_trimmed_muted_and_published(hero_client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(hero_service, "run_job", lambda *a: None)  # the background task uses its own session
    muertos = by_slug(state(hero_client), "dia-de-muertos")
    data = sample_video(tmp_path / "in.mp4", seconds=20, size="1000x1000")  # square, with audio, 20 s
    r = upload(hero_client, muertos["id"], data)
    assert r.status_code == 202, r.text
    assert by_slug(r.json(), "dia-de-muertos")["status"] == "processing"
    assert upload(hero_client, muertos["id"], data).status_code == 409  # already processing

    root = Path(get_settings().media_root)
    originals = list((root / "originales" / "hero").glob("dia-de-muertos-*.src"))
    assert len(originals) == 1 and originals[0].stat().st_mode & 0o777 == 0o600
    hero_service.finish(db_session, campaign(db_session, "dia-de-muertos").id, root / "publico", originals[0])

    c = campaign(db_session, "dia-de-muertos")
    assert c.processing_status == "ready" and c.video_mp4.startswith("hero/dia-de-muertos/")
    assert not originals[0].exists()
    mp4 = root / "publico" / c.video_mp4
    info = probe(mp4)
    assert info["codec_type"] == "video" and "audio" not in info.values()
    assert float(info["duration"]) <= 12.5
    assert (int(info["width"]), int(info["height"])) == (1000, 562)  # 16:9 centred crop of 1000x1000
    assert probe(root / "publico" / c.video_webm)["codec_type"] == "video"
    assert (root / "publico" / c.poster).stat().st_size > 0

    # Served from /media/ and offered by the public endpoint once published.
    served = hero_client.get(f"/media/{c.video_mp4}")
    assert served.status_code == 200 and served.headers["content-type"] == "video/mp4"
    assert hero_client.get(f"/media/{c.poster}").status_code == 200
    r = hero_client.post(f"/api/admin/v1/hero/campaigns/{c.id}/publish", headers=JSON(), json={})
    assert r.status_code == 200
    assert hero_service.current(db_session, date(2026, 10, 25))[0].id == c.id

    # Replacing it removes the old files after the new ones exist.
    old = {c.video_mp4, c.video_webm, c.poster}
    data2 = sample_video(tmp_path / "in2.mp4", seconds=3, size="640x360", audio=False)
    assert upload(hero_client, c.id, data2).status_code == 202
    new_original = next((root / "originales" / "hero").glob("dia-de-muertos-*.src"))
    hero_service.finish(db_session, c.id, root / "publico", new_original)
    db_session.refresh(c)
    assert c.processing_status == "ready" and not (old & {c.video_mp4, c.video_webm, c.poster})
    assert not any((root / "publico" / p).exists() for p in old)
    assert (root / "publico" / c.video_mp4).exists()
    actions = [e.action for e in db_session.execute(select(AuditEvent).where(AuditEvent.entity_id == c.id)).scalars()]
    assert "hero.video_uploaded" in actions and "hero.video_ready" in actions


@needs_ffmpeg
def test_a_broken_video_is_recorded_as_an_error_and_keeps_the_old_one(hero_client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(hero_service, "run_job", lambda *a: None)
    c = make_ready(db_session, "navidad")
    r = upload(hero_client, c.id, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200)  # looks like MP4, is not
    assert r.status_code == 202
    root = Path(get_settings().media_root)
    original = next((root / "originales" / "hero").glob("navidad-*.src"))
    hero_service.finish(db_session, c.id, root / "publico", original)
    db_session.refresh(c)
    assert c.processing_status == "error" and c.processing_error
    assert c.video_mp4 == "hero/navidad/aaaaaaaaaaaa.mp4"  # the previous video is untouched
    assert "Se conserva el video anterior" in c.processing_error
    c.published = True
    assert hero_service.current(db_session, date(2026, 12, 20))[0] == c  # and still shows
    body = state(hero_client)
    assert by_slug(body, "navidad")["status"] == "error" and by_slug(body, "navidad")["error"]


def test_upload_rejects_what_is_not_a_video(hero_client):
    navidad = by_slug(state(hero_client), "navidad")
    if not FFMPEG:
        pytest.skip("ffmpeg is not installed")
    r = upload(hero_client, navidad["id"], b"GIF89a" + b"\x00" * 50)
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_media_type"
    r = upload(hero_client, navidad["id"], b"x" * 20, content_type="text/html")
    assert r.status_code == 415
    r = hero_client.post(f"/api/admin/v1/hero/campaigns/{navidad['id']}/video", content=b"x",
                         headers={"Content-Type": "video/mp4", **auth(make_token(email=OWNER))})
    assert r.status_code == 403  # no CSRF header


def test_upload_limits_and_missing_pieces(hero_client, monkeypatch):
    navidad = by_slug(state(hero_client), "navidad")
    if FFMPEG:
        monkeypatch.setattr(hero_service, "MAX_ORIGINAL_BYTES", 100)
        r = upload(hero_client, navidad["id"], b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200)
        assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    monkeypatch.setattr(hero_service, "ffmpeg_available", lambda: False)
    r = upload(hero_client, navidad["id"], b"\x00\x00\x00\x18ftypmp42")
    assert r.status_code == 503 and r.json()["error"]["code"] == "ffmpeg_unavailable"
    assert state(hero_client)["ffmpeg_available"] is False


def test_only_hero_accounts_may_upload(hero_client):
    navidad = by_slug(state(hero_client), "navidad")
    assert upload(hero_client, navidad["id"], b"\x00\x00\x00\x18ftypmp42", email=EDITOR).status_code == 403
