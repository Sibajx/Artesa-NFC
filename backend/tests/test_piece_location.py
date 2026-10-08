"""P-026 G12: where a piece physically is, with its history of moves."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.models.audit_event import AuditEvent
from app.models.piece_location import PieceLocation
from tests.test_admin_api import auth, client, make_token, verifier  # noqa: F401  (fixtures)
from tests.test_admin_writes import H, act, new_artisan, new_piece

TODAY = date.today().isoformat()


def move(client, piece, **body):
    return client.post(f"/api/admin/v1/pieces/{piece['id']}/location", json={"moved_on": TODAY, **body},
                       headers=H(**{"If-Match": piece["updated_at"]}))


def a_piece(client):
    artisan = new_artisan(client, name="Rigoberto Ramírez Robles")
    return new_piece(client, artisan["id"], name="El Viejito")


def test_moves_build_a_history_newest_first(client, db_session):
    piece = a_piece(client)
    assert client.get(f"/api/admin/v1/pieces/{piece['id']}", headers=auth(make_token())).json()["locations"] == []

    first = move(client, piece, location="taller", note="Recién terminada")
    assert first.status_code == 200, first.text
    second = move(client, first.json(), location="exhibicion", place="Museo de los Pintores Oaxaqueños")
    assert second.status_code == 200, second.text
    locations = second.json()["locations"]
    assert [m["location"] for m in locations] == ["exhibicion", "taller"]
    assert locations[0]["place"] == "Museo de los Pintores Oaxaqueños" and locations[0]["recorded_by"]
    assert locations[1]["note"] == "Recién terminada"

    events = db_session.execute(select(AuditEvent).where(AuditEvent.action == "piece.moved")
                                .order_by(AuditEvent.occurred_at)).scalars().all()
    assert [(e.event_metadata["from"], e.event_metadata["to"]) for e in events[-2:]] == [
        (None, "taller"), ("taller", "exhibicion")]


def test_location_is_gestion_only(client):
    artisan = new_artisan(client, name="Ubicación Privada")
    piece = new_piece(client, artisan["id"], name="Máscara de diablo")
    act(client, "artisans", artisan, "publish")
    published = act(client, "pieces", piece, "publish").json()
    assert move(client, published, location="bodega", place="Bodega de Cuilápam").status_code == 200
    public = client.get(f"/api/v1/pieces/{published['slug']}")
    assert public.status_code == 200
    assert "locations" not in public.json() and "Bodega" not in public.text


def test_rules(client):
    piece = a_piece(client)
    assert move(client, piece, location="luna").status_code == 422
    future = (date.today() + timedelta(days=1)).isoformat()
    r = move(client, piece, location="tienda", moved_on=future)
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_location"
    # If-Match: a stale version is refused.
    assert move(client, piece, location="tienda").status_code == 200
    assert move(client, piece, location="bodega").status_code == 412
    # Without the admin write header nothing moves.
    r = client.post(f"/api/admin/v1/pieces/{piece['id']}/location", json={"location": "tienda", "moved_on": TODAY},
                    headers={**auth(make_token()), "If-Match": piece["updated_at"]})
    assert r.status_code == 403


def test_trashed_piece_cannot_move_and_purge_takes_the_history(client, db_session):
    piece = move(client, a_piece(client), location="taller").json()
    trashed = act(client, "pieces", piece, "trash").json()
    assert move(client, trashed, location="bodega").json()["error"]["code"] == "trashed"
    assert act(client, "pieces", trashed, "purge").status_code == 204
    assert db_session.execute(select(PieceLocation).where(PieceLocation.piece_id == piece["id"])).first() is None
