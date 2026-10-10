"""Permission checkboxes (PR 1): roles keep meaning what they meant, and an
account with an explicit list gets exactly that list."""
from __future__ import annotations

import uuid

from sqlalchemy import update

from app.core import permissions as perms
from app.core.access import AdminIdentity
from app.models.admin_account import AdminAccount
from tests.test_accounts_and_authorization import FIXED, NEW, OWNER, H, owner_client  # noqa: F401
from tests.test_admin_api import auth, make_token

API = "/api/admin/v1"
ALL = set(perms.ALL_PERMISSIONS)


# --- the mapping: no role changes what it could do ----------------------------------


def test_roles_map_to_what_they_could_always_do():
    editor = {"view", "edit", "publish", "authorization", "sales", "logistics"}
    assert perms.for_roles({"editor"}) == editor
    assert perms.for_roles({"editor", "designer"}) == editor | {"design"}
    assert perms.for_roles({"editor", "designer", "custodian"}) == editor | {"design", "nfc", "revocations"}
    assert perms.for_roles({"editor", "hero"}) == editor | {"hero"}
    assert perms.for_roles({"editor", "designer", "hero"}) == editor | {"design", "hero"}
    assert perms.for_roles({"editor", "owner"}) == ALL
    assert perms.for_roles({"unknown"}) == frozenset()
    # Nobody has Capacitaciones until the module exists, except the owner.
    assert "training" not in perms.for_roles({"editor", "designer", "custodian", "hero"})


def test_an_explicit_list_replaces_the_roles_but_never_the_owner():
    plain = AdminIdentity(email="a@x.org", roles=frozenset({"editor", "custodian"}))
    assert plain.can("nfc") and plain.can("revocations") and not plain.can("training")
    narrowed = AdminIdentity(email="a@x.org", roles=frozenset({"editor", "custodian"}),
                             granted=frozenset({"view", "sales"}))
    assert narrowed.permissions == {"view", "sales"}
    owner = AdminIdentity(email=OWNER, roles=frozenset({"editor", "owner"}), granted=frozenset({"view"}))
    assert owner.permissions == ALL


def test_clean_drops_unknown_names():
    assert perms.clean(["view", "bogus", "sales"]) == {"view", "sales"}


# --- through the API -----------------------------------------------------------------


def _managed(owner_client, db_session, role="editor", permissions=None):
    r = owner_client.post(f"{API}/accounts", json={"email": NEW, "role": role}, headers=H())
    assert r.status_code == 200, r.text
    if permissions is not None:
        db_session.execute(update(AdminAccount).where(AdminAccount.email == NEW).values(permissions=permissions))
        db_session.commit()


def me(client, email=NEW):
    return client.get(f"{API}/me", headers=auth(make_token(email=email))).json()


def test_me_lists_the_permissions_and_nothing_changes_without_an_explicit_list(owner_client, db_session):
    _managed(owner_client, db_session, "custodian")
    assert set(me(owner_client)["permissions"]) == perms.for_roles({"editor", "designer", "custodian"})
    assert set(me(owner_client, OWNER)["permissions"]) == ALL
    assert set(me(owner_client, FIXED)["permissions"]) == perms.for_roles({"editor"})


def test_an_account_with_a_narrow_list_only_does_what_it_lists(owner_client, db_session):
    _managed(owner_client, db_session, "editor", ["view", "sales", "logistics"])
    assert set(me(owner_client)["permissions"]) == {"view", "sales", "logistics"}
    h = {**auth(make_token(email=NEW)), "X-Artesa-Admin": "1"}
    # Reading stays open to anyone with access.
    assert owner_client.get(f"{API}/artisans", headers=h).status_code == 200
    # Editing, publishing, authorization, design, hero and custody are closed.
    assert owner_client.post(f"{API}/artisans", json={"full_name": "María López Ruiz"}, headers=h).status_code == 403
    pid = uuid.uuid4()
    for path in (f"/pieces/{pid}/publish", f"/artisans/{pid}/authorization/request", f"/pieces/{pid}/availability"):
        assert owner_client.post(f"{API}{path}", json={}, headers=h).status_code == 403, path
    assert owner_client.get(f"{API}/hero", headers=h).status_code == 403
    assert owner_client.get(f"{API}/pieces/{pid}/designs", headers=h).status_code == 403
    assert owner_client.get(f"{API}/custody/pieces", headers=h).status_code == 403
    # Sales and location are open: they get past the gate (the piece does not exist: 404, not 403).
    assert owner_client.post(f"{API}/pieces/{pid}/sale", json={}, headers=h).status_code != 403
    assert owner_client.post(f"{API}/pieces/{pid}/location", json={}, headers=h).status_code != 403


def test_custody_splits_nfc_from_revocations(owner_client, db_session):
    _managed(owner_client, db_session, "editor", ["view", "nfc"])
    h = {**auth(make_token(email=NEW)), "X-Artesa-Admin": "1"}
    pid = uuid.uuid4()
    assert owner_client.get(f"{API}/custody/pieces", headers=h).status_code == 200
    assert owner_client.post(f"{API}/custody/pieces/{pid}/revoke", json={"reason": "robo"}, headers=h).status_code == 403
    assert owner_client.post(f"{API}/custody/pieces/{pid}/stolen", json={"note": "x" * 20}, headers=h).status_code == 403
    # And the other way round: revocations alone can still open the area.
    db_session.execute(update(AdminAccount).where(AdminAccount.email == NEW).values(permissions=["view", "revocations"]))
    db_session.commit()
    assert owner_client.post(f"{API}/custody/pieces/{pid}/revoke", json={"reason": "robo"}, headers=h).status_code != 403
    assert owner_client.post(f"{API}/custody/pieces/{pid}/issue", json={}, headers=h).status_code == 403


def test_a_fixed_account_with_a_row_is_narrowed_by_it(owner_client, db_session):
    db_session.add(AdminAccount(email=FIXED, role="editor", active=True, added_by=OWNER, permissions=["view"]))
    db_session.commit()
    assert set(me(owner_client, FIXED)["permissions"]) == {"view"}
    # ...but never the owner.
    db_session.add(AdminAccount(email=OWNER, role="editor", active=True, added_by=OWNER, permissions=["view"]))
    db_session.commit()
    assert set(me(owner_client, OWNER)["permissions"]) == ALL


# --- PR 2: the owner's matrix --------------------------------------------------------


def post(client, path, body=None, email=OWNER):
    return client.post(f"{API}{path}", json=body if body is not None else {}, headers=H(email))


def listing(client):
    return {a["email"]: a for a in client.get(f"{API}/accounts", headers=auth(make_token(email=OWNER))).json()["data"]}


def test_the_list_shows_what_each_account_can_do(owner_client, db_session):
    _managed(owner_client, db_session, "designer")
    body = owner_client.get(f"{API}/accounts", headers=auth(make_token(email=OWNER))).json()
    assert body["catalog"] == list(perms.ALL_PERMISSIONS)
    assert set(body["presets"]["custodian"]) == perms.for_roles({"custodian"})
    rows = {a["email"]: a for a in body["data"]}
    assert set(rows[OWNER]["permissions"]) == ALL and rows[OWNER]["custom"] is False
    assert set(rows[NEW]["permissions"]) == perms.for_roles({"designer"}) and rows[NEW]["custom"] is False
    assert rows[FIXED]["imported"] is False


def test_importing_the_fixed_accounts_changes_nobodys_access(owner_client, db_session):
    before = {e: set(me(owner_client, e)["permissions"]) for e in (FIXED, OWNER)}
    r = post(owner_client, "/accounts/import-fixed")
    assert r.status_code == 200, r.text
    rows = {a["email"]: a for a in r.json()["data"]}
    assert rows[FIXED]["imported"] is True and rows[FIXED]["custom"] is True and rows[OWNER]["imported"] is False
    assert {e: set(me(owner_client, e)["permissions"]) for e in (FIXED, OWNER)} == before
    # The owner has no row, and doing it again creates nothing.
    assert db_session.get(AdminAccount, OWNER) is None
    assert post(owner_client, "/accounts/import-fixed").status_code == 200
    assert db_session.query(AdminAccount).filter_by(email=FIXED).count() == 1


def test_the_owner_edits_the_checkboxes(owner_client, db_session):
    post(owner_client, "/accounts/import-fixed")
    r = post(owner_client, f"/accounts/{FIXED}/permissions", {"permissions": ["view", "sales", "logistics"]})
    assert r.status_code == 200, r.text
    assert set(me(owner_client, FIXED)["permissions"]) == {"view", "sales", "logistics"}
    h = {**auth(make_token(email=FIXED)), "X-Artesa-Admin": "1"}
    assert owner_client.post(f"{API}/artisans", json={"full_name": "María López Ruiz"}, headers=h).status_code == 403
    # Back to what the role gives.
    r = post(owner_client, f"/accounts/{FIXED}/permissions", {"permissions": None})
    assert {a["email"]: a for a in r.json()["data"]}[FIXED]["custom"] is False
    assert set(me(owner_client, FIXED)["permissions"]) == perms.for_roles({"editor"})


def test_the_matrix_refuses_bad_input(owner_client, db_session):
    _managed(owner_client, db_session, "editor")
    path = f"/accounts/{NEW}/permissions"
    assert post(owner_client, path, {"permissions": ["view", "bogus"]}).json()["error"]["code"] == "invalid_permission"
    assert post(owner_client, path, {"permissions": ["sales"]}).json()["error"]["code"] == "view_required"
    assert post(owner_client, f"/accounts/{OWNER}/permissions", {"permissions": ["view"]}).json()["error"]["code"] == "owner_account"
    # A fixed account must be imported first; an unknown one does not exist.
    assert post(owner_client, f"/accounts/{FIXED}/permissions", {"permissions": ["view"]}).json()["error"]["code"] == "not_imported"
    assert post(owner_client, "/accounts/nadie@example.org/permissions", {"permissions": ["view"]}).status_code == 404
    # Only the owner.
    assert post(owner_client, path, {"permissions": ["view"]}, email=FIXED).status_code == 403
    assert post(owner_client, "/accounts/import-fixed", email=FIXED).status_code == 403


def test_picking_a_role_is_a_shortcut_that_drops_the_custom_list(owner_client, db_session):
    _managed(owner_client, db_session, "editor")
    post(owner_client, f"/accounts/{NEW}/permissions", {"permissions": ["view", "hero"]})
    assert set(me(owner_client)["permissions"]) == {"view", "hero"}
    post(owner_client, f"/accounts/{NEW}/role", {"role": "designer"})
    assert set(me(owner_client)["permissions"]) == perms.for_roles({"designer"})
    assert listing(owner_client)[NEW]["custom"] is False
    # A fixed account's role is not changed from here, imported or not.
    post(owner_client, "/accounts/import-fixed")
    assert post(owner_client, f"/accounts/{FIXED}/role", {"role": "custodian"}).json()["error"]["code"] == "fixed_account"
