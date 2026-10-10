"""P-026 G11: inventory and certification state as CSV."""
from __future__ import annotations

import csv
import io

from sqlalchemy import select

from app.models.audit_event import AuditEvent
from tests.test_admin_api import CUSTODIAN_EMAIL, auth, client, make_token, verifier  # noqa: F401  (fixtures)
from tests.test_admin_custody_nfc import UID, post as custody
from tests.test_admin_writes import H, act, new_artisan, new_piece
from tests.test_sales import SALE, post as piece_post, published


def export(c, email=None):
    token = make_token(email=email) if email else make_token()
    return c.get("/api/admin/v1/exports/pieces.csv", headers=auth(token))


def rows(response) -> list[dict]:
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.DictReader(io.StringIO(text[1:])))


def test_inventory_columns_for_every_admin(client, db_session):
    piece = published(client)
    priced = client.patch(f"/api/admin/v1/pieces/{piece['id']}", json={"price_cents": 420050},
                          headers=H(**{"If-Match": piece["updated_at"]})).json()
    moved = client.post(f"/api/admin/v1/pieces/{piece['id']}/location",
                        json={"location": "tienda", "place": "Andador Turístico", "moved_on": SALE["sold_on"]},
                        headers=H(**{"If-Match": priced["updated_at"]})).json()
    assert piece_post(client, moved, "sale", SALE).status_code == 200

    r = export(client)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-disposition"].startswith('attachment; filename="artesanfc-piezas-')
    row = next(x for x in rows(r) if x["Código"] == piece["public_code"])
    assert row["Pieza"] == "El Negrito" and row["Artesano"] == "Rigoberto Ramírez Robles"
    assert row["Publicación"] == "Publicado" and row["Disponibilidad"] == "Vendida"
    assert row["Precio"] == "4200.50" and row["Moneda"] == "MXN"
    assert row["Fecha de venta"] == SALE["sold_on"] and row["Precio de venta"] == "3500.00"
    assert row["Canal de venta"] == "taller"
    assert row["Ubicación"] == "Tienda" and row["Lugar"] == "Andador Turístico"
    # The buyer's personal data never leaves the sale row.
    assert "Ana López" not in r.text and "ana@example.com" not in r.text
    # Certification belongs to custodians (ADR-030).
    assert "Certificado" not in row

    event = db_session.execute(select(AuditEvent).where(AuditEvent.action == "export.pieces")).scalars().all()[-1]
    assert event.event_metadata["custody_columns"] is False and event.event_metadata["rows"] >= 1


def test_custodian_gets_certification_columns(client):
    piece = published(client)
    custody(client, piece, "issue", {"uid": UID})
    row = next(x for x in rows(export(client, email=CUSTODIAN_EMAIL)) if x["Código"] == piece["public_code"])
    assert row["Certificado"] == "Activo" and row["Versión del certificado"] == "1"
    assert row["Chip"] in {"Disponible", "Programado"}
    assert row["Con dueño"] == "No" and row["Reportada como robada"] == "No"


def test_formulas_are_neutralised_and_trash_left_out(client):
    artisan = new_artisan(client, name="Prueba CSV")
    evil = new_piece(client, artisan["id"], name="=HYPERLINK(\"http://x\")", materials=["Madera", "Pintura"])
    gone = new_piece(client, artisan["id"], name="En la papelera")
    act(client, "pieces", gone, "trash")
    body = rows(export(client))
    names = {x["Pieza"] for x in body}
    assert "'=HYPERLINK(\"http://x\")" in names
    assert "En la papelera" not in names
    assert next(x for x in body if x["Código"] == evil["public_code"])["Materiales"] == "Madera, Pintura"


def test_requires_admin(client):
    assert client.get("/api/admin/v1/exports/pieces.csv").status_code in {401, 403, 404}
