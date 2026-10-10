"""Stock of supplies (ledger) and the inventory of pieces."""
from __future__ import annotations

import uuid

from sqlalchemy import select, update

from app.models.admin_account import AdminAccount
from app.models.audit_event import AuditEvent
from app.models.supply import SupplyMovement
from tests.test_accounts_and_authorization import FIXED, NEW, OWNER, H, owner_client  # noqa: F401
from tests.test_admin_api import auth, make_token

API = "/api/admin/v1"


def post(client, path, body=None, email=OWNER):
    return client.post(f"{API}{path}", json=body if body is not None else {}, headers=H(email))


def get(client, path, email=OWNER):
    return client.get(f"{API}{path}", headers=auth(make_token(email=email)))


def new_supply(client, name="Chips NTAG213", unit="pieza", min_stock=10, **extra):
    r = post(client, "/supplies", {"name": name, "unit": unit, "min_stock": min_stock, **extra})
    assert r.status_code == 201, r.text
    return r.json()["supply"]


def move(client, supply, kind, quantity, **extra):
    return post(client, f"/supplies/{supply['id']}/movements", {"kind": kind, "quantity": quantity, **extra})


def stock(client, supply):
    return float(get(client, f"/supplies/{supply['id']}").json()["supply"]["stock"])


def test_the_stock_is_the_sum_of_the_movements(owner_client):
    s = new_supply(owner_client)
    assert stock(owner_client, s) == 0
    assert move(owner_client, s, "purchase", 50, unit_cost_cents=1250).status_code == 201
    assert move(owner_client, s, "use", 3).status_code == 201
    assert move(owner_client, s, "loss", 2, note="Se dañaron al programar").status_code == 201
    assert move(owner_client, s, "adjustment", 1, direction="up", note="Conteo físico").status_code == 201
    assert stock(owner_client, s) == 46
    detail = get(owner_client, f"/supplies/{s['id']}").json()
    assert [m["kind"] for m in detail["movements"]] == ["adjustment", "loss", "use", "purchase"]
    assert detail["movements"][-1]["unit_cost_cents"] == 1250 and float(detail["movements"][1]["delta"]) == -2


def test_the_stock_never_goes_below_zero(owner_client):
    s = new_supply(owner_client, min_stock=0)
    move(owner_client, s, "purchase", 5)
    r = move(owner_client, s, "use", 6)
    assert r.status_code == 409 and r.json()["error"]["code"] == "insufficient_stock"
    assert move(owner_client, s, "adjustment", 6, direction="down", note="x" * 5).json()["error"]["code"] == "insufficient_stock"
    assert stock(owner_client, s) == 5
    assert move(owner_client, s, "use", 5).status_code == 201 and stock(owner_client, s) == 0


def test_rules_of_each_kind(owner_client):
    s = new_supply(owner_client)
    move(owner_client, s, "purchase", 10)
    assert move(owner_client, s, "loss", 1).json()["error"]["code"] == "note_required"
    assert move(owner_client, s, "adjustment", 1).json()["error"]["code"] == "note_required"
    assert move(owner_client, s, "use", 1, unit_cost_cents=100).json()["error"]["code"] == "invalid_movement"
    assert move(owner_client, s, "purchase", 1, piece_id=str(uuid.uuid4())).json()["error"]["code"] == "invalid_movement"
    assert move(owner_client, s, "use", 1, piece_id=str(uuid.uuid4())).status_code == 404
    assert post(owner_client, f"/supplies/{s['id']}/movements", {"kind": "use", "quantity": 0}).status_code == 422
    assert post(owner_client, f"/supplies/{s['id']}/movements", {"kind": "gift", "quantity": 1}).status_code == 422
    assert stock(owner_client, s) == 10


def test_a_use_can_name_the_piece(owner_client):
    artisan = post(owner_client, "/artisans", {"full_name": "María López Ruiz"}).json()
    piece = post(owner_client, "/pieces", {"artisan_id": artisan["id"], "name": "Máscara de tigre"}).json()
    s = new_supply(owner_client)
    move(owner_client, s, "purchase", 4)
    assert move(owner_client, s, "use", 1, piece_id=piece["id"]).status_code == 201
    m = get(owner_client, f"/supplies/{s['id']}").json()["movements"][0]
    assert m["piece_id"] == piece["id"] and m["piece_name"] == "Máscara de tigre"


def test_low_stock_and_the_catalogue(owner_client, db_session):
    chips = new_supply(owner_client, min_stock=10)
    epoxy = new_supply(owner_client, name="Epoxi", unit="ml", min_stock=0)
    move(owner_client, chips, "purchase", 12)
    body = get(owner_client, "/supplies").json()
    rows = {x["name"]: x for x in body["data"]}
    assert rows["Chips NTAG213"]["low"] is False and rows["Epoxi"]["low"] is False and body["low_count"] == 0
    move(owner_client, chips, "use", 2)
    body = get(owner_client, "/supplies").json()
    assert {x["name"]: x["low"] for x in body["data"]}["Chips NTAG213"] is True and body["low_count"] == 1
    # Duplicate names, in any case, are refused.
    r = post(owner_client, "/supplies", {"name": "chips ntag213", "unit": "pieza"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate"
    # A deactivated supply leaves the list and takes no movements.
    assert owner_client.patch(f"{API}/supplies/{epoxy['id']}", json={"active": False}, headers=H()).status_code == 200
    assert "Epoxi" not in {x["name"] for x in get(owner_client, "/supplies").json()["data"]}
    assert "Epoxi" in {x["name"] for x in get(owner_client, "/supplies?include_inactive=true").json()["data"]}
    assert move(owner_client, epoxy, "purchase", 1).json()["error"]["code"] == "inactive_supply"


def test_editing_a_supply(owner_client):
    s = new_supply(owner_client)
    r = owner_client.patch(f"{API}/supplies/{s['id']}", json={"min_stock": 25, "note": "Pedir a Mercado Libre"}, headers=H())
    assert r.status_code == 200 and float(r.json()["supply"]["min_stock"]) == 25
    assert owner_client.patch(f"{API}/supplies/{s['id']}", json={"name": "  "}, headers=H()).json()["error"]["code"] == "invalid_supply"
    assert owner_client.patch(f"{API}/supplies/{uuid.uuid4()}", json={"note": "x"}, headers=H()).status_code == 404


def test_every_change_is_audited_and_the_ledger_is_append_only(owner_client, db_session):
    s = new_supply(owner_client)
    move(owner_client, s, "purchase", 5)
    actions = {a for (a,) in db_session.execute(select(AuditEvent.action).where(AuditEvent.entity_type == "supply"))}
    assert {"supply.created", "supply.purchase"} <= actions
    assert db_session.execute(select(SupplyMovement)).scalars().first().recorded_by == OWNER


def test_who_can_read_and_who_can_write(owner_client, db_session):
    s = new_supply(owner_client)
    post(owner_client, "/accounts", {"email": NEW, "role": "editor"})
    # Everyone with access reads; an editor holds Logistics, so writes too.
    assert get(owner_client, "/supplies", FIXED).status_code == 200
    assert post(owner_client, f"/supplies/{s['id']}/movements", {"kind": "purchase", "quantity": 1}, NEW).status_code == 201
    # Without Logistics: reads yes, writes no.
    db_session.execute(update(AdminAccount).where(AdminAccount.email == NEW).values(permissions=["view", "sales"]))
    db_session.commit()
    assert get(owner_client, "/supplies", NEW).status_code == 200
    assert get(owner_client, "/inventory", NEW).status_code == 200
    assert post(owner_client, "/supplies", {"name": "Sellos", "unit": "pieza"}, NEW).status_code == 403
    assert post(owner_client, f"/supplies/{s['id']}/movements", {"kind": "purchase", "quantity": 1}, NEW).status_code == 403
    assert owner_client.patch(f"{API}/supplies/{s['id']}", json={"note": "x"}, headers=H(NEW)).status_code == 403


def test_the_inventory_of_pieces(owner_client):
    # Other tests may leave committed pieces in the database: compare against what was there.
    before = get(owner_client, "/inventory").json()["summary"]
    artisan = post(owner_client, "/artisans", {"full_name": "María López Ruiz"}).json()
    post(owner_client, "/pieces", {"artisan_id": artisan["id"], "name": "Máscara de tigre", "price_cents": 150000})
    post(owner_client, "/pieces", {"artisan_id": artisan["id"], "name": "Alebrije", "price_cents": 90000})
    body = get(owner_client, "/inventory").json()
    after = body["summary"]
    assert after["total"] == before["total"] + 2
    assert after["by_availability"].get("available", 0) == before["by_availability"].get("available", 0) + 2
    assert after["available_value_cents"] == before["available_value_cents"] + 240000 and after["currency"] == "MXN"
    row = next(x for x in body["data"] if x["name"] == "Alebrije" and x["artisan_id"] == artisan["id"])
    assert row["artisan_name"] == "María López Ruiz" and row["price_cents"] == 90000 and row["location"] is None
