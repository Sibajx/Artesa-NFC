"""P-026 G5-G8: Resumen data by role, audit filters, Certificación list columns."""
from __future__ import annotations

from tests.test_admin_api import CUSTODIAN_EMAIL, auth, client, make_token, verifier  # noqa: F401
from tests.test_admin_custody_nfc import UID, post as custody, published_piece
from tests.test_admin_writes import H, act, new_artisan
from tests.test_ownership_card import card


def get(c, path, email=None, **params):
    token = make_token(email=email) if email else make_token()
    return c.get(f"/api/admin/v1/{path}", params=params, headers=auth(token))


def ids(bucket) -> set[str]:
    return {i["id"] for i in bucket["items"]}


def test_summary_is_shaped_by_role(client):
    piece = published_piece(client)  # its artisan is published without a registered authorization
    artisan_id = piece["artisan"]["id"] if "artisan" in piece else get(client, f"pieces/{piece['id']}").json()["artisan"]["id"]
    editor = get(client, "summary").json()
    assert artisan_id in ids(editor["published_artisans_without_authorization"])
    assert "published_without_certificate" not in editor and "designs_in_review" not in editor
    keeper = get(client, "summary", email=CUSTODIAN_EMAIL).json()
    assert piece["id"] in ids(keeper["published_without_certificate"])
    assert "designs_in_review" in keeper

    custody(client, piece, "issue", {"uid": UID})
    keeper = get(client, "summary", email=CUSTODIAN_EMAIL).json()
    assert piece["id"] not in ids(keeper["published_without_certificate"])
    assert piece["id"] in ids(keeper["certified_without_chip"]) and piece["id"] in ids(keeper["certified_without_card"])
    card(client, piece)
    keeper = get(client, "summary", email=CUSTODIAN_EMAIL).json()
    assert piece["id"] not in ids(keeper["certified_without_card"])
    assert set(keeper["sales_last_30_days"]) == {"count", "total_cents"}


def test_artisan_answers_show_up(client):
    piece = published_piece(client)
    d = client.post(f"/api/admin/v1/pieces/{piece['id']}/designs", json={}, headers={
        **auth(make_token(email=CUSTODIAN_EMAIL)), "X-Artesa-Admin": "1"}).json()
    sent = client.post(f"/api/admin/v1/designs/{d['id']}/submit", json={}, headers={
        **auth(make_token(email=CUSTODIAN_EMAIL)), "X-Artesa-Admin": "1", "If-Match": d["updated_at"]}).json()
    token = sent["review_url"].split("#", 1)[1]
    client.post("/api/v1/design-reviews/decision", json={"token": token, "decision": "changes", "comment": "Más rojo"})
    s = get(client, "summary", email=CUSTODIAN_EMAIL).json()
    assert s["recent_answers"][0]["text"] == "El artesano de «El Negrito» pidió cambios al diseño del certificado"
    assert s["recent_answers"][0]["positive"] is False
    assert s["designs_with_changes_requested"]["count"] == 1
    # The page links to the piece, not to the design row.
    assert s["designs_with_changes_requested"]["items"][0]["id"] == piece["id"]


def test_audit_filters(client):
    a = new_artisan(client, name="Filtro Uno")
    act(client, "artisans", a, "publish")
    all_events = get(client, "audit-events").json()["data"]
    assert {e["action"] for e in all_events} >= {"artisan.created", "artisan.published"}
    only = get(client, "audit-events", action_prefix="artisan.pub").json()["data"]
    assert {e["action"] for e in only} == {"artisan.published"}
    assert get(client, "audit-events", actor_email="OPS@example.org").json()["data"]
    assert get(client, "audit-events", actor_email="nadie@example.org").json()["data"] == []
    assert get(client, "audit-events", since="2000-01-01", until="2000-01-02").json()["data"] == []
    assert get(client, "audit-events", action_prefix="a'; drop").status_code == 422


def test_custody_list_columns(client):
    piece = published_piece(client)
    custody(client, piece, "issue", {"uid": UID})
    card(client, piece)
    row = next(r for r in get(client, "custody/pieces", email=CUSTODIAN_EMAIL).json()["data"] if r["id"] == piece["id"])
    assert row["card_status"] == "active" and row["claimed"] is False
    assert row["design_status"] is None and row["sold"] is False and row["reported_stolen"] is False
