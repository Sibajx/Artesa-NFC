"""ADR-030 phase 5b: the team's artwork on the original certificate."""
from __future__ import annotations

import base64
import io

from PIL import Image

from app.services import certificate_render as renderer
from app.services import designs
from tests.test_admin_api import auth, client, make_token, verifier  # noqa: F401  (fixtures)
from tests.test_admin_custody_nfc import CH, published_piece
from tests.test_designs import PARAMS, act, new_design, patch


def png(size=(400, 300), alpha=True, extra: bytes = b"") -> bytes:
    image = Image.new("RGBA" if alpha else "RGB", size, (200, 30, 40, 128) if alpha else (200, 30, 40))
    out = io.BytesIO()
    image.save(out, "PNG", pnginfo=None)
    return out.getvalue() + extra


def upload(client, data: bytes, content_type="image/png", headers=None):
    return client.post("/api/admin/v1/certificate-art", content=data,
                       headers={**(headers or CH()), "Content-Type": content_type})


def test_upload_reencodes_and_is_content_addressed(client):
    r = upload(client, png(size=(3000, 1500), extra=b"<script>trailing junk</script>"))
    assert r.status_code == 201, r.text
    art = r.json()
    assert art["mime_type"] == "image/png" and max(art["width"], art["height"]) == 1000
    # Same pixels again: same id, no duplicate.
    assert upload(client, png(size=(3000, 1500))).json()["id"] == art["id"]
    # Opaque artwork is stored as JPEG to keep certificates light.
    assert upload(client, png(alpha=False)).json()["mime_type"] == "image/jpeg"


def test_upload_rules(client):
    assert upload(client, b"GIF89a....", content_type="image/gif").status_code == 415
    assert upload(client, b"<svg xmlns='http://www.w3.org/2000/svg'/>", content_type="image/png").status_code == 422
    assert upload(client, b"").status_code == 422
    # Editors (no designer role) cannot upload; neither can a request without the write header.
    assert upload(client, png(), headers={**auth(make_token()), "X-Artesa-Admin": "1"}).status_code == 403
    assert upload(client, png(), headers=auth(make_token())).status_code in {403}


def test_art_in_a_design_preview_and_frozen_version(client):
    art = upload(client, png()).json()
    piece = published_piece(client)
    d = new_design(client, piece)
    for placement in renderer.ART_PLACEMENTS:
        r = client.post("/api/admin/v1/designs/preview",
                        json={"params": {**PARAMS, "art": {"id": art["id"], "placement": placement, "opacity": 0.2}}},
                        headers=CH())
        assert r.status_code == 200 and "data:image/png;base64," in r.json()["svg"]
    unknown = {"id": "0" * 64, "placement": "sello"}
    assert patch(client, d, {"art": unknown}).json()["error"]["code"] == "invalid_design"
    assert patch(client, d, {"art": {"id": art["id"], "placement": "techo"}}).json()["error"]["code"] == "invalid_design"
    assert patch(client, d, {"art": {"id": art["id"], "opacity": 2}}).json()["error"]["code"] == "invalid_design"

    d = patch(client, d, {"art": {"id": art["id"], "placement": "encabezado"}}).json()
    assert d["params"]["art"] == {"id": art["id"], "placement": "encabezado", "opacity": 0.15}
    assert "data:image/png;base64," in d["svg"]
    d = act(client, d, "approve", {"name": "Rigoberto", "medium": "en persona", "note": "Le gustó el logo"}).json()
    published = act(client, d, "publish").json()
    assert published["status"] == "published" and "data:image/png;base64," in published["svg"]

    # Removing the art from a later draft leaves the published version as it was.
    d2 = patch(client, new_design(client, piece), {"art": None}).json()
    assert "art" not in d2["params"] and "data:image" not in d2["svg"]
    v1 = client.get(f"/api/admin/v1/designs/{published['id']}", headers=CH()).json()
    assert v1["svg"] == published["svg"]


def test_renderer_only_embeds_the_server_side_uri():
    params = designs.clean_params({**PARAMS, "art": {"id": "a" * 64, "placement": "fondo", "opacity": 0.3}})
    # Without the stored art (no data URI) nothing is embedded, whatever the params say.
    assert "<image" not in renderer.render(params, version=1)
    uri = "data:image/png;base64," + base64.b64encode(png()).decode()
    svg = renderer.render(params, version=1, art_uri=uri)
    assert svg.count("<image") == 1 and 'opacity="0.30"' in svg and "http" not in svg.replace("http://www.w3.org/2000/svg", "")
