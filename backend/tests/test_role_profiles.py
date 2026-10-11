"""Role profiles: the owner edits named sets of checkboxes; an account shows the one it matches exactly."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select, update

from app.core import permissions as perms
from app.models.admin_account import AdminAccount
from app.models.admin_role_profile import AdminRoleProfile
from app.models.audit_event import AuditEvent
from tests.test_accounts_and_authorization import FIXED, NEW, OWNER, H, owner_client  # noqa: F401
from tests.test_admin_api import auth, make_token
from tests.test_permissions import _managed, listing, me, post

API = "/api/admin/v1"


def test_the_migration_seeds_the_five_roles(owner_client):
    seeded = profiles(owner_client)
    assert list(seeded) == ["Administración", "Certificador", "Diseño y Marca", "Operaciones", "Consulta"]
    assert seeded["Administración"]["permissions"] == list(perms.ALL_PERMISSIONS)
    assert seeded["Consulta"]["permissions"] == ["view"]
    # What was agreed with the PO for the team.
    assert seeded["Diseño y Marca"]["permissions"] == [p for p in perms.ALL_PERMISSIONS if p in {
        "view", "edit", "publish", "sales", "logistics", "design", "hero", "visits", "production"}]
    assert seeded["Operaciones"]["permissions"] == [p for p in perms.ALL_PERMISSIONS if p in {
        "view", "sales", "logistics", "training", "visits", "production"}]


@pytest.fixture()
def empty(db_session):
    """Starts each test without the seeded profiles (the test rolls back)."""
    db_session.execute(delete(AdminRoleProfile))
    db_session.commit()


def profiles(client):
    body = client.get(f"{API}/accounts", headers=auth(make_token(email=OWNER))).json()
    return {p["name"]: p for p in body["profiles"]}


def test_the_owner_creates_edits_and_deletes_a_profile(owner_client, db_session, empty):
    r = post(owner_client, "/roles", {"name": "  Diseño   y Marca ", "description": "Catálogo y diseño",
                                       "permissions": ["design", "view", "hero"]})
    assert r.status_code == 200, r.text
    made = profiles(owner_client)["Diseño y Marca"]
    assert made["slug"] == "diseno-y-marca"
    # The checkboxes come back in the fixed order, whatever order they were sent in.
    assert made["permissions"] == ["view", "design", "hero"]

    r = post(owner_client, f"/roles/{made['slug']}", {"name": "Marca", "description": None,
                                                      "permissions": ["view", "design", "hero", "visits"]})
    assert r.status_code == 200, r.text
    assert "Diseño y Marca" not in profiles(owner_client)
    assert profiles(owner_client)["Marca"]["permissions"] == ["view", "design", "hero", "visits"]

    assert post(owner_client, f"/roles/{made['slug']}/delete").status_code == 200
    assert "Marca" not in profiles(owner_client)
    actions = {a for a in db_session.execute(select(AuditEvent.action).where(AuditEvent.entity_type == "admin_role")).scalars()}
    assert actions == {"role.created", "role.updated", "role.deleted"}


def test_a_profile_needs_a_name_view_and_known_checkboxes(owner_client, empty):
    ok = {"name": "Operaciones", "description": None, "permissions": ["view"]}
    assert post(owner_client, "/roles", ok).status_code == 200
    assert post(owner_client, "/roles", {**ok, "name": "operaciones"}).status_code == 409  # same name, any case
    assert post(owner_client, "/roles", {**ok, "name": "X"}).status_code == 409
    assert post(owner_client, "/roles", {**ok, "name": "Otro", "permissions": ["sales"]}).status_code == 409  # no "view"
    assert post(owner_client, "/roles", {**ok, "name": "Otro", "permissions": ["view", "bogus"]}).status_code == 409
    assert post(owner_client, "/roles/nope", ok).status_code == 404
    assert post(owner_client, "/roles/nope/delete").status_code == 404


def test_only_the_owner_manages_profiles(owner_client, db_session):
    _managed(owner_client, db_session, "custodian")
    body = {"name": "Intruso", "description": None, "permissions": ["view"]}
    assert owner_client.post(f"{API}/roles", json=body, headers=H(NEW)).status_code == 403
    assert owner_client.get(f"{API}/accounts", headers=auth(make_token(email=NEW))).status_code == 403


def test_an_account_shows_the_profile_with_exactly_its_checkboxes(owner_client, db_session, empty):
    post(owner_client, "/roles", {"name": "Operaciones", "description": None,
                                  "permissions": ["view", "sales", "logistics", "production"]})
    _managed(owner_client, db_session, "editor", ["production", "logistics", "sales", "view"])
    rows = listing(owner_client)
    assert rows[NEW]["profile"] == "Operaciones"  # same set, any order
    assert rows[OWNER]["profile"] == "Dueño"

    db_session.execute(update(AdminAccount).where(AdminAccount.email == NEW)
                       .values(permissions=["view", "sales", "logistics"]))
    db_session.commit()
    assert listing(owner_client)[NEW]["profile"] is None  # one short: "A medida"

    # Editing the profile changes the match, never anyone's access.
    post(owner_client, "/roles/operaciones", {"name": "Operaciones", "description": None,
                                              "permissions": ["view", "sales", "logistics"]})
    assert listing(owner_client)[NEW]["profile"] == "Operaciones"
    assert set(me(owner_client)["permissions"]) == {"view", "sales", "logistics"}


def test_picking_a_profile_copies_its_checkboxes_to_the_account(owner_client, db_session, empty):
    post(owner_client, "/roles", {"name": "Certificador", "description": None,
                                  "permissions": ["view", "authorization", "nfc", "revocations", "design"]})
    _managed(owner_client, db_session, "editor")
    pick = profiles(owner_client)["Certificador"]["permissions"]
    assert post(owner_client, f"/accounts/{NEW}/permissions", {"permissions": pick}).status_code == 200
    assert listing(owner_client)[NEW]["profile"] == "Certificador"


def test_production_has_its_own_checkbox(owner_client, db_session):
    # Logística alone no longer lets you mark production steps; Producción does. The board is open to Ver.
    _managed(owner_client, db_session, "editor", ["view", "logistics"])
    h = {**auth(make_token(email=NEW)), "X-Artesa-Admin": "1"}
    step = f"{API}/production/pieces/{uuid.uuid4()}/steps/received"
    assert owner_client.get(f"{API}/production/board", headers=h).status_code == 200
    assert owner_client.post(step, json={}, headers=h).status_code == 403
    db_session.execute(update(AdminAccount).where(AdminAccount.email == NEW).values(permissions=["view", "production"]))
    db_session.commit()
    assert owner_client.post(step, json={}, headers=h).status_code != 403
    # The old roles keep both, so nobody lost the board.
    assert "production" in perms.for_roles({"editor"})
