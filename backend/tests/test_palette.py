"""ADR-030 phase 4: the piece's palette (piece.visual_theme) from its cover photo."""
from __future__ import annotations

import io

import random

from PIL import Image, ImageDraw
from sqlalchemy import select

from app.models.audit_event import AuditEvent
from app.services import palette
from tests.test_admin_api import auth, client, make_token, verifier  # noqa: F401  (fixtures)
from tests.test_admin_media import media_root, piece, upload  # noqa: F401  (fixtures)
from tests.test_admin_writes import H

WOOD, CLAY, CEMPASUCHIL = (92, 63, 40), (138, 75, 47), (242, 163, 58)


def striped(colors=(WOOD, CLAY, CEMPASUCHIL), size=(900, 600)) -> bytes:
    image = Image.new("RGB", size)
    band = size[1] // len(colors)
    for i, color in enumerate(colors):
        image.paste(color, (0, i * band, size[0], size[1] if i == len(colors) - 1 else (i + 1) * band))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=95)
    return out.getvalue()


def get(client, record):
    return client.get(f"/api/admin/v1/pieces/{record['id']}", headers=auth(make_token())).json()


def near(hex_color: str, rgb, tolerance=24) -> bool:
    value = tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return all(abs(a - b) <= tolerance for a, b in zip(value, rgb))


def test_extract_finds_the_main_colours(tmp_path):
    path = tmp_path / "p.jpg"
    path.write_bytes(striped())
    colors = palette.extract(path)
    assert palette.MIN_COLORS <= len(colors) <= palette.MAX_COLORS
    assert all(palette.HEX_RE.fullmatch(c) for c in colors)
    for rgb in (WOOD, CLAY, CEMPASUCHIL):
        assert any(near(c, rgb) for c in colors), (rgb, colors)


def mask_photo() -> bytes:
    """Like a real cover: a dark mask in the middle of a textured brown
    background, with a small red mouth and smaller white eyes and teeth."""
    rng = random.Random(7)
    image = Image.new("RGB", (900, 675))
    px = image.load()
    for y in range(675):
        for x in range(900):
            base = (104, 84, 61) if (x // 30 + y // 45) % 3 else (64, 46, 32)
            px[x, y] = tuple(max(0, min(255, c + rng.randint(-18, 18))) for c in base)
    d = ImageDraw.Draw(image)
    d.ellipse((248, 90, 652, 615), fill=(16, 12, 11))
    d.ellipse((315, 150, 420, 248), fill=(70, 66, 64))
    d.ellipse((352, 285, 405, 322), fill=(236, 230, 220))
    d.ellipse((495, 285, 548, 322), fill=(236, 230, 220))
    d.ellipse((405, 450, 495, 518), fill=(200, 24, 30))
    d.rectangle((431, 469, 469, 488), fill=(240, 236, 228))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90)
    return out.getvalue()


def test_mask_photo_keeps_its_small_accents_and_leads_with_the_piece(tmp_path):
    """El Negrito (2026-10-04): area alone gave five browns and greys and lost
    the red mouth and white eyes that identify the piece."""
    path = tmp_path / "mask.jpg"
    path.write_bytes(mask_photo())
    colors = palette.extract(path)
    assert near(colors[0], (16, 12, 11), 30), colors          # the mask, not the background
    assert near(colors[1], (200, 24, 30), 40), colors         # the red mouth as the accent
    assert any(near(c, (236, 230, 220), 30) for c in colors), colors  # white eyes / teeth
    assert any(near(c, (84, 65, 46), 45) for c in colors), colors     # the wood/earth


def test_flat_photo_is_completed_with_shades(tmp_path):
    path = tmp_path / "flat.jpg"
    path.write_bytes(striped(colors=((120, 90, 60),)))
    colors = palette.extract(path)
    assert len(colors) == palette.MIN_COLORS and len(set(colors)) == palette.MIN_COLORS


def test_first_photo_fills_the_palette_and_manual_wins(client, db_session, media_root, piece):  # noqa: F811
    before = get(client, piece)
    assert before["visual_theme"] is None
    assert upload(client, "pieces", piece, striped(), "hero").status_code == 201
    after = get(client, piece)
    theme = after["visual_theme"]
    # The automatic fill keeps the piece version: the editor's next save is not stale.
    assert after["updated_at"] == before["updated_at"]
    assert theme["palette_source"] == "auto" and any(near(c, CEMPASUCHIL) for c in theme["palette"])

    # A second photo never replaces an existing palette on its own.
    upload(client, "pieces", piece, striped(colors=((10, 10, 200), (200, 10, 10), (10, 200, 10))), "gallery")
    assert get(client, piece)["visual_theme"]["palette"] == theme["palette"]

    current = get(client, piece)
    manual = ["#111111", "#C9761C", "#efe4cf"]
    r = client.post(f"/api/admin/v1/pieces/{piece['id']}/palette", json={"colors": manual},
                    headers=H(**{"If-Match": current["updated_at"]}))
    assert r.status_code == 200, r.text
    assert r.json()["visual_theme"] == {"palette": ["#111111", "#c9761c", "#efe4cf"], "palette_source": "manual"}

    # Regenerating from the cover is explicit and returns to "auto".
    r = client.post(f"/api/admin/v1/pieces/{piece['id']}/palette/generate", json={},
                    headers=H(**{"If-Match": r.json()["updated_at"]}))
    assert r.status_code == 200 and r.json()["visual_theme"]["palette_source"] == "auto"

    actions = db_session.execute(select(AuditEvent.action).where(AuditEvent.action == "piece.palette_set")).scalars().all()
    assert len(actions) == 3


def test_palette_rules(client, media_root, piece):  # noqa: F811
    current = get(client, piece)
    url = f"/api/admin/v1/pieces/{piece['id']}"
    no_photo = client.post(f"{url}/palette/generate", json={}, headers=H(**{"If-Match": current["updated_at"]}))
    assert no_photo.status_code == 409 and no_photo.json()["error"]["code"] == "no_cover_photo"
    for bad in (["#111111", "#222222"], ["#111111", "#222222", "red"], ["#111111"] * 6):
        r = client.post(f"{url}/palette", json={"colors": bad}, headers=H(**{"If-Match": current["updated_at"]}))
        assert r.status_code in (409, 422) and r.json()["error"]["code"] in ("invalid_palette", "validation_error")
    stale = client.post(f"{url}/palette", json={"colors": ["#111111", "#222222", "#333333"]},
                        headers=H(**{"If-Match": "2020-01-01T00:00:00+00:00"}))
    assert stale.status_code == 412 and stale.json()["error"]["code"] == "stale"


def test_public_piece_exposes_the_palette(client, db_session, media_root, piece):  # noqa: F811
    from tests.test_admin_writes import act
    upload(client, "pieces", piece, striped(), "hero")
    artisan = client.get(f"/api/admin/v1/artisans/{get(client, piece)['artisan']['id']}", headers=auth(make_token())).json()
    act(client, "artisans", artisan, "publish")
    act(client, "pieces", get(client, piece), "publish")
    public = client.get(f"/api/v1/pieces/{piece['slug']}").json()
    assert public["visual_theme"]["palette_source"] == "auto" and len(public["visual_theme"]["palette"]) >= 3
