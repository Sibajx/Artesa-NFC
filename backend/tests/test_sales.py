"""P-026: list price and the sale of a piece."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.models.audit_event import AuditEvent
from tests.test_admin_api import auth, client, make_token, verifier  # noqa: F401  (fixtures)
from tests.test_admin_writes import H, act, new_artisan, new_piece

SALE = {"sold_on": "2026-10-04", "price_cents": 350000, "currency": "MXN", "channel": "taller",
        "sold_by": "Rigoberto Ramírez", "buyer_name": "Ana López", "buyer_contact": "ana@example.com",
        "note": "Pagó en efectivo"}


def get(client, piece):
    return client.get(f"/api/admin/v1/pieces/{piece['id']}", headers=auth(make_token())).json()


def post(client, piece, path, body):
    return client.post(f"/api/admin/v1/pieces/{piece['id']}/{path}", json=body,
                       headers=H(**{"If-Match": piece["updated_at"]}))


def published(client):
    artisan = new_artisan(client, name="Rigoberto Ramírez Robles")
    piece = new_piece(client, artisan["id"], name="El Negrito")
    act(client, "artisans", artisan, "publish")
    return act(client, "pieces", piece, "publish").json()


def test_list_price_is_gestion_only(client):
    piece = published(client)
    r = client.patch(f"/api/admin/v1/pieces/{piece['id']}", json={"price_cents": 420000, "price_currency": "MXN"},
                     headers=H(**{"If-Match": piece["updated_at"]}))
    assert r.status_code == 200 and r.json()["price_cents"] == 420000
    bad = client.patch(f"/api/admin/v1/pieces/{piece['id']}", json={"price_cents": -1},
                       headers=H(**{"If-Match": r.json()["updated_at"]}))
    assert bad.status_code == 422
    public = client.get(f"/api/v1/pieces/{piece['slug']}").json()
    assert "price_cents" not in public and "price_currency" not in public


def test_register_and_cancel_a_sale(client, db_session):
    piece = published(client)
    sold = post(client, piece, "sale", SALE)
    assert sold.status_code == 200, sold.text
    body = sold.json()
    assert body["availability_status"] == "sold"
    assert body["sales"][0]["status"] == "active" and body["sales"][0]["buyer_name"] == "Ana López"
    assert client.get(f"/api/v1/pieces/{piece['slug']}").json()["availability_status"] == "sold"

    assert post(client, body, "sale", SALE).json()["error"]["code"] == "already_sold"
    # "sold" never comes from the availability control, nor leaves through it.
    assert post(client, body, "availability", {"availability_status": "available"}).json()["error"]["code"] == "use_sale"

    assert post(client, body, "sale/cancel", {"reason": "no"}).status_code == 422
    cancelled = post(client, body, "sale/cancel", {"reason": "El comprador se arrepintió"}).json()
    assert cancelled["availability_status"] == "available"
    assert [s["status"] for s in cancelled["sales"]] == ["cancelled"]
    assert post(client, cancelled, "sale/cancel", {"reason": "otra vez"}).json()["error"]["code"] == "not_sold"

    events = db_session.execute(select(AuditEvent).where(AuditEvent.action.in_(("piece.sold", "piece.sale_cancelled")))
                                ).scalars().all()
    assert {e.action for e in events} == {"piece.sold", "piece.sale_cancelled"}
    blob = " ".join(str(e.event_metadata) for e in events)
    assert "Ana" not in blob and "ana@example.com" not in blob  # buyer data stays out of the audit


def test_sale_rules(client):
    piece = published(client)
    future = (date.today() + timedelta(days=2)).isoformat()
    assert post(client, piece, "sale", {**SALE, "sold_on": future}).json()["error"]["code"] == "invalid_sale"
    assert post(client, piece, "sale", {**SALE, "channel": "trueque"}).status_code == 422
    assert post(client, piece, "sale", {**SALE, "price_cents": -5}).status_code == 422
    assert post(client, piece, "availability", {"availability_status": "sold"}).json()["error"]["code"] == "use_sale"
    artisan = new_artisan(client, name="Otro Taller")
    r = client.post("/api/admin/v1/pieces", json={"artisan_id": artisan["id"], "name": "Directa", "availability_status": "sold"},
                    headers=H())
    assert r.json()["error"]["code"] == "use_sale"


def test_a_piece_with_a_sale_cannot_be_purged(client):
    artisan = new_artisan(client, name="Taller Borrador")
    piece = new_piece(client, artisan["id"], name="Vendida en borrador")
    piece = post(client, piece, "sale", SALE).json()
    piece = post(client, piece, "sale/cancel", {"reason": "Venta de prueba"}).json()
    piece = client.post(f"/api/admin/v1/pieces/{piece['id']}/trash", json={},
                        headers=H(**{"If-Match": piece["updated_at"]})).json()
    assert piece["purge_blocker"] == "has_sale"
