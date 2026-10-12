"""ADR-030 phase 5: designed original certificates — render, workflow, review link, publication."""
from __future__ import annotations

import re

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.models.audit_event import AuditEvent
from app.models.certificate_design import CertificateDesign
from app.services import certificate_render as renderer
from app.services import designs
from tests.test_admin_api import CUSTODIAN_EMAIL, auth, client, make_token, verifier  # noqa: F401
from tests.test_admin_custody_nfc import CH, published_piece
from tests.test_admin_writes import H
from tests.test_ownership_card import card, certified, unlock

PARAMS = {"template": "greca", "variant": "claro", "title": "Certificado original", "piece_name": "El Negrito",
          "artisan_name": "Rigoberto", "public_code": "ANFC-X", "quote": "La tallé con madera de un árbol caído.",
          "palette": ["#130e0e", "#d50919", "#f6ede3", "#685f67"], "seed": 7}


def reader():
    return auth(make_token(email=CUSTODIAN_EMAIL))


def new_design(client, piece):
    r = client.post(f"/api/admin/v1/pieces/{piece['id']}/designs", json={}, headers=CH())
    assert r.status_code == 201, r.text
    return r.json()


def patch(client, design, params):
    return client.patch(f"/api/admin/v1/designs/{design['id']}", json={"params": params},
                        headers=CH(**{"If-Match": design["updated_at"]}))


def act(client, design, action, body=None):
    return client.post(f"/api/admin/v1/designs/{design['id']}/{action}", json=body or {},
                       headers=CH(**{"If-Match": design["updated_at"]}))


def review(client, token, decision=None, comment=None):
    if decision is None:
        return client.post("/api/v1/design-reviews/resolve", json={"token": token}).json()
    return client.post("/api/v1/design-reviews/decision",
                       json={"token": token, "decision": decision, "comment": comment}).json()


def test_render_is_safe_and_deterministic():
    hostile = {**PARAMS, "quote": '<script>alert(1)</script> & "x"', "piece_name": "<img src=x onerror=1>"}
    svg = renderer.render(designs.clean_params(hostile), version=1)
    assert "<script" not in svg and "<img" not in svg and "&lt;script&gt;" in svg
    assert svg.startswith("<svg") and "http://" not in svg.replace("http://www.w3.org/2000/svg", "")
    a = renderer.render(designs.clean_params(PARAMS), version=1)
    assert a == renderer.render(designs.clean_params(PARAMS), version=1)
    assert a != renderer.render(designs.clean_params({**PARAMS, "seed": 8}), version=1)
    for template in renderer.TEMPLATES:
        assert renderer.render(designs.clean_params({**PARAMS, "template": template}), version=2).endswith("</svg>")


def test_params_are_validated():
    for bad in ({"palette": ["#111111"]}, {"palette": ["red", "#111111", "#222222"]}, {"quote": "x" * 241},
                {"title": 5}):
        try:
            designs.clean_params({**PARAMS, **bad})
        except designs.ContentConflict as exc:
            assert exc.code == "invalid_design"
        else:
            raise AssertionError(bad)
    cleaned = designs.clean_params({**PARAMS, "template": "otra", "seed": -3, "evil": "x"})
    assert cleaned["template"] == "clasico" and cleaned["seed"] == 1 and "evil" not in cleaned


def test_full_workflow_with_review_link(client, db_session):
    piece = published_piece(client)
    assert client.post(f"/api/admin/v1/pieces/{piece['id']}/designs", json={}, headers=H()).status_code == 403
    assert client.get(f"/api/admin/v1/pieces/{piece['id']}/designs", headers=auth(make_token())).status_code == 403

    d = new_design(client, piece)
    assert d["version"] == 1 and d["status"] == "draft" and "BORRADOR" in d["svg"]
    assert d["params"]["piece_name"] == "El Negrito" and d["params"]["public_code"] == piece["public_code"]
    assert client.post(f"/api/admin/v1/pieces/{piece['id']}/designs", json={},
                       headers=CH()).json()["error"]["code"] == "open_design_exists"

    preview = client.post("/api/admin/v1/designs/preview", json={"params": PARAMS}, headers=CH())
    assert preview.status_code == 200 and preview.json()["svg"].startswith("<svg")

    d = patch(client, d, {"template": "greca", "quote": "La tallé con madera de un árbol caído."}).json()
    assert d["template"] == "greca"

    sent = act(client, d, "submit").json()
    assert sent["status"] == "in_review" and "EN REVISIÓN" in sent["svg"]
    url = sent["review_url"]
    assert url.startswith("http://127.0.0.1:5500/revision/#")
    token = url.split("#", 1)[1]
    opened = review(client, token)
    assert opened["status"] == "open" and opened["piece_name"] == "El Negrito" and opened["svg"].startswith("<svg")

    # The artisan asks for changes: back to draft, link dead.
    assert review(client, token, "changes", "Ponle mi frase completa")["status"] == "recorded"
    assert review(client, token) == {"status": "unavailable"}
    d = client.get(f"/api/admin/v1/designs/{d['id']}", headers=reader()).json()
    assert d["status"] == "draft" and d["change_request"] == "Ponle mi frase completa"

    d = patch(client, d, {"quote": "Cada máscara guarda la fiesta de mi pueblo."}).json()
    assert d["change_request"] is None
    token = act(client, d, "submit").json()["review_url"].split("#", 1)[1]
    assert review(client, token, "approve")["status"] == "recorded"
    d = client.get(f"/api/admin/v1/designs/{d['id']}", headers=reader()).json()
    assert d["status"] == "approved" and d["approval_medium"] == "enlace" and d["approved_by_name"]
    assert "BORRADOR" not in d["svg"] and "EN REVISIÓN" not in d["svg"]

    assert patch(client, d, {"quote": "otra"}).json()["error"]["code"] == "design_frozen"
    d = act(client, d, "publish").json()
    assert d["status"] == "published" and d["published_by"] == CUSTODIAN_EMAIL

    # A redesign is version 2, starting from version 1's params; v1 stays published until v2 is.
    d2 = new_design(client, piece)
    assert d2["version"] == 2 and d2["params"]["quote"] == "Cada máscara guarda la fiesta de mi pueblo."
    bad = act(client, d2, "approve", {"name": "Rigoberto", "medium": "paloma", "note": "ok"})
    assert bad.json()["error"]["code"] == "invalid_approval"
    d2 = act(client, d2, "approve", {"name": "Rigoberto", "medium": "en persona", "note": "Lo vio impreso en el taller"}).json()
    assert d2["status"] == "approved" and d2["approval_recorded_by"] == CUSTODIAN_EMAIL
    act(client, d2, "publish")
    statuses = {x["version"]: x["status"] for x in client.get(f"/api/admin/v1/pieces/{piece['id']}/designs",
                                                              headers=reader()).json()["data"]}
    assert statuses == {1: "superseded", 2: "published"}

    # No review token in the audit trail.
    blob = " ".join(str(e.event_metadata) for e in db_session.execute(
        select(AuditEvent).where(AuditEvent.action.like("design.%"))).scalars())
    assert token not in blob and "design.published" in {e.action for e in db_session.execute(
        select(AuditEvent).where(AuditEvent.action.like("design.%"))).scalars()}


def test_review_link_expires_and_discard(client, db_session):
    piece = published_piece(client)
    d = new_design(client, piece)
    sent = act(client, d, "submit").json()
    token = sent["review_url"].split("#", 1)[1]
    db_session.execute(update(CertificateDesign).where(CertificateDesign.id == d["id"])
                       .values(review_expires_at=func.now() - timedelta(minutes=1)))
    db_session.commit()
    assert review(client, token) == {"status": "unavailable"}
    assert review(client, token, "approve") == {"status": "unavailable"}
    current = client.get(f"/api/admin/v1/designs/{d['id']}", headers=reader()).json()
    assert act(client, current, "discard").status_code == 204
    assert client.get(f"/api/admin/v1/pieces/{piece['id']}/designs", headers=reader()).json()["data"] == []


def test_unlocked_original_carries_the_published_design(client):
    piece, token = certified(client)
    key = card(client, piece)["key"]
    assert unlock(client, token, key).json()["design"] is None
    d = new_design(client, piece)
    d = act(client, d, "approve", {"name": "Rigoberto", "medium": "whatsapp", "note": "Mandó un audio aprobándolo"}).json()
    act(client, d, "publish")
    original = unlock(client, token, key).json()
    assert original["design"]["version"] == 1 and original["design"]["svg"].startswith("<svg")
    assert original["design"]["approved_by_name"] == "Rigoberto"


def test_review_endpoints_have_the_body_limit_and_no_store(client):
    r = client.post("/api/v1/design-reviews/resolve", json={"token": "nope"})
    assert r.status_code == 200 and r.json() == {"status": "unavailable"} and r.headers["cache-control"] == "no-store"
    big = client.post("/api/v1/design-reviews/decision", content=b'{"token":"' + b"a" * 2000 + b'"}',
                      headers={"content-type": "application/json"})
    assert big.status_code == 413


def test_an_approved_unpublished_design_can_be_discarded_to_start_again(client, db_session):
    piece = published_piece(client)
    d = new_design(client, piece)
    d = act(client, d, "approve", {"name": "Rigoberto", "medium": "en persona", "note": "Lo vio en el taller"}).json()
    assert d["status"] == "approved"
    assert new_design_conflict(client, piece) == "open_design_exists"
    assert act(client, d, "discard").status_code == 204
    again = new_design(client, piece)
    assert again["version"] == 1 and again["status"] == "draft"
    event = db_session.execute(select(AuditEvent).where(AuditEvent.action == "design.discarded")).scalars().one()
    assert event.event_metadata["status"] == "approved" and event.event_metadata["approved_by"] == "Rigoberto"
    # A published version is history: it cannot be discarded.
    again = act(client, again, "approve", {"name": "Rigoberto", "medium": "whatsapp", "note": "Mandó un audio"}).json()
    published = act(client, again, "publish").json()
    assert act(client, published, "discard").json()["error"]["code"] == "design_frozen"


def new_design_conflict(client, piece) -> str:
    return client.post(f"/api/admin/v1/pieces/{piece['id']}/designs", json={}, headers=CH()).json()["error"]["code"]


def test_the_approval_date_is_mexico_local_time(client, db_session):
    from datetime import datetime, timezone
    piece = published_piece(client)
    d = new_design(client, piece)
    d = act(client, d, "approve", {"name": "Rigoberto", "medium": "en persona", "note": "Lo vio en el taller"}).json()
    design = designs.CertificateDesign(params=d["params"], version=1, status=designs.DesignStatus.approved,
                                       approved_at=datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc),
                                       approved_by_name="Rigoberto")
    # 03:00 UTC on the 5th is still the 4th in Oaxaca (UTC-6).
    assert "04/10/2026" in designs.svg(db_session, design)


def test_motion_is_scoped_to_allowed_motion_and_static_at_rest():
    svg = renderer.render(designs.clean_params({**PARAMS, "template": "greca"}), version=1)
    assert "<style>@media (prefers-reduced-motion:no-preference){" in svg
    # Outside the media query nothing animates: the resting sheet is complete.
    assert svg.count("@keyframes") == 14 and svg.count("animation:") == 17
    assert svg.index("@media") < svg.index("animation:") and "<script" not in svg and "<set" not in svg
    # Waves and shine are invisible at rest; the greca is fully stroked at rest.
    assert svg.count('class="m-wave') == 3 and 'class="m-greca" pathLength="1"' in svg


def test_edition_label_and_validation():
    one = renderer.render(designs.clean_params(PARAMS), version=1)
    assert "PIEZA ÚNICA" in one and "PIEZA 3 DE" not in one
    numbered = renderer.render(designs.clean_params({**PARAMS, "edition": {"number": 3, "total": 10}}), version=1)
    assert "PIEZA 3 DE 10" in numbered and "PIEZA ÚNICA" not in numbered
    assert designs.clean_params({**PARAMS, "edition": {"number": 3, "total": 10}})["edition"] == {"number": 3, "total": 10}
    assert "edition" not in designs.clean_params({**PARAMS, "edition": None})
    for bad in ({"number": 0, "total": 5}, {"number": 6, "total": 5}, {"number": 1, "total": 10000},
                {"number": "1", "total": 5}, {"number": True, "total": 5}, {"number": 1}, "3 de 10"):
        try:
            designs.clean_params({**PARAMS, "edition": bad})
        except designs.ContentConflict as exc:
            assert exc.code == "invalid_design"
        else:
            raise AssertionError(bad)


STORY = {"enabled": True, "technique": "Barro negro bruñido", "materials": "barro de San Bartolo, cuarzo",
         "place": "San Bartolo Coyotepec, Oaxaca"}


def test_the_story_block_only_prints_when_turned_on_and_is_validated():
    base = designs.clean_params({**PARAMS, "template": "clasico"})
    off = renderer.render(designs.clean_params({**PARAMS, "template": "clasico", "story": {**STORY, "enabled": False}}), version=1)
    on = renderer.render(designs.clean_params({**PARAMS, "template": "clasico", "story": STORY}), version=1)
    # A design saved before the block existed renders exactly as before; "off" prints nothing of it.
    assert "story" not in base and "OFICIO" not in renderer.render(base, version=1)
    assert "Barro negro" not in off and "OFICIO" not in off
    assert "OFICIO" in on and "Barro negro bruñido" in on and "MATERIALES" in on and "San Bartolo Coyotepec" in on
    assert "<script" not in renderer.render(designs.clean_params({**PARAMS, "story": {**STORY, "place": "<script>x</script>"}}), version=1)
    assert designs.clean_params({**PARAMS, "story": STORY})["story"] == STORY
    for bad in ("texto", {"technique": 5}, {"technique": "x" * 61}, {"materials": "x" * 91}, {"place": "x" * 81}):
        try:
            designs.clean_params({**PARAMS, "story": bad})
        except designs.ContentConflict as exc:
            assert exc.code == "invalid_design"
        else:
            raise AssertionError(bad)


def test_story_rows_that_would_touch_the_seal_are_left_out():
    long_name = {**PARAMS, "template": "clasico", "piece_name": "Máscara de tigre con listones de colores", "story": STORY,
                 "quote": "Una frase larga " * 14}
    svg = renderer.render(designs.clean_params(long_name), version=1)
    assert svg.endswith("</svg>") and svg.count("OFICIO") <= 1 and svg.count("LUGAR") == 0


def test_the_telar_template_draws_two_woven_bands_and_keeps_the_motion_rules():
    svg = renderer.render(designs.clean_params({**PARAMS, "template": "telar"}), version=1)
    assert "telar" in renderer.TEMPLATES and svg.endswith("</svg>") and svg.count("<path d=\"M") > 20
    assert "<script" not in svg and "<set" not in svg and 'class="m-greca"' not in svg and svg.count('class="m-wave') == 3
    assert svg == renderer.render(designs.clean_params({**PARAMS, "template": "telar"}), version=1)
    assert designs.clean_params({**PARAMS, "template": "telar"})["template"] == "telar"
    dark = renderer.render(designs.clean_params({**PARAMS, "template": "telar", "variant": "oscuro"}), version=1)
    assert dark != svg


@pytest.mark.parametrize("template", ["amate", "barro", "mitla", "alebrije"])
def test_the_regional_templates_render_static_and_deterministic_in_both_variants(template):
    params = designs.clean_params({**PARAMS, "template": template})
    svg = renderer.render(params, version=1)
    assert template in renderer.TEMPLATES and params["template"] == template and svg.endswith("</svg>")
    assert "<script" not in svg and "<set" not in svg and 'class="m-greca"' not in svg and svg.count('class="m-wave') == 3
    assert svg == renderer.render(params, version=1)
    assert renderer.render({**params, "variant": "oscuro"}, version=1) != svg
    with_art = renderer.render({**params, "art": {"placement": "encabezado"}}, version=1, art_uri="data:image/png;base64,AAAA")
    assert "<image" in with_art


def test_the_seal_is_a_tesseract_of_three_stage_layers_with_a_still_frame_at_rest():
    svg = renderer.render(designs.clean_params({**PARAMS, "template": "clasico"}), version=1)
    # One SMIL animation on `d`, looping back to its first frame, and a still copy for reduced motion.
    assert svg.count("<animate ") == 1 and 'attributeName="d"' in svg and 'repeatCount="indefinite"' in svg
    values = svg.split('values="', 1)[1].split('"', 1)[0].split(";")
    assert len(values) == renderer._TESS_FRAMES + 1 and values[0] == values[-1] and len(set(values)) == renderer._TESS_FRAMES
    assert 'class="m-tess-static"' in svg and 'class="m-tess-live"' in svg and 'display="none"' in svg
    assert svg.count('<use href="#tess-rest"') == 3 and svg.count('<use href="#tess-live"') == 3
    # The stages are written on the seal, and the seal is not an official one.
    for word in ("CHIP", "EMPAQUE", "ENVÍO", "ORIGINAL"):
        assert word in svg
    assert "ECONOM" not in svg.upper() and "<script" not in svg and "http" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert svg == renderer.render(designs.clean_params({**PARAMS, "template": "clasico"}), version=1) and len(svg) < 60_000


def test_the_tesseract_takes_the_dominant_colour_of_the_piece_and_the_frame_light_always_runs():
    red = renderer.render(designs.clean_params({**PARAMS, "template": "clasico", "variant": "claro",
                                                "palette": ["#1d1915", "#d62839", "#efe4cf", "#5c3f28"]}), version=1)
    assert '<g class="m-tess-static" color="#d62839"' in red and red.count('class="m-flow') == 4
    dark = renderer.render(designs.clean_params({**PARAMS, "template": "clasico", "variant": "oscuro",
                                                 "palette": ["#1d1915", "#d62839", "#efe4cf", "#5c3f28"]}), version=1)
    assert '<g class="m-tess-static" color="#d62839"' not in dark and dark != red
    # Rest state: the light, the motes and the sheen are invisible until the motion rules show them.
    assert red.count('opacity="0"') >= 4 + 16 + 1


def test_the_sheet_rises_in_turn_the_name_gets_a_holographic_band_and_the_layers_light_up_in_sequence():
    svg = renderer.render(designs.clean_params({**PARAMS, "template": "clasico"}), version=1)
    # Every block rises in its own beat; the name carries a band clipped to its letters, invisible at rest.
    assert svg.count('class="m-rise"') >= 5 and 'clip-path="url(#name-clip)"' in svg
    assert 'class="m-holo"' in svg and 'opacity="0"' in svg.split('class="m-holo"', 1)[1].split(">", 1)[0]
    # Chip (inner layer) lights first, then packing, then shipping.
    assert svg.count('class="m-light"') == 3
    assert [int(d) for d in re.findall(r'class="m-light" style="animation-delay:(\d+)s"', svg)] == [6, 3, 0]
    # The words are still all there at rest.
    assert PARAMS["artisan_name"] in svg and svg.count(">por ") == 1


def test_the_sheet_does_not_print_the_design_version_but_keeps_the_code_and_the_approval():
    draft = renderer.render(designs.clean_params({**PARAMS, "template": "clasico"}), version=3)
    assert "DISEÑO" not in draft and "versión 3" not in draft and PARAMS["public_code"].upper() in draft.upper()
    approved = renderer.render(designs.clean_params({**PARAMS, "template": "clasico"}), version=3,
                               approved_by="Rigoberto", approved_on="04/10/2026")
    assert "APROBADO POR" in approved and "Rigoberto" in approved and "DISEÑO" not in approved
