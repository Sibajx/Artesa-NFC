"""ADR-030 phase 5: designed original certificates — render, workflow, review link, publication."""
from __future__ import annotations

from datetime import timedelta

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
