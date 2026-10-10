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


def test_fixed_accounts_ignore_a_stray_explicit_list(owner_client, db_session):
    # A row for an email that is also in ADMIN_EMAILS never narrows it.
    db_session.add(AdminAccount(email=FIXED, role="editor", active=True, added_by=OWNER, permissions=["view"]))
    db_session.commit()
    assert set(me(owner_client, FIXED)["permissions"]) == perms.for_roles({"editor"})
