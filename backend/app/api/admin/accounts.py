"""P-026 G4: the owner's "Usuarios" page (OWNER_EMAILS only; others 403)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.admin.writes import _fail, actor, require_write_guard
from app.api.deps import get_db
from app.core import permissions as perms
from app.core.access import FORBIDDEN_ERROR, OWNER, AdminIdentity, require_admin
from app.core.config import get_settings
from app.services import admin_accounts
from app.services.content import Actor, ContentError


def require_owner(identity: AdminIdentity = Depends(require_admin)) -> AdminIdentity:
    if not identity.has(OWNER):
        raise HTTPException(status_code=403, detail=FORBIDDEN_ERROR)
    return identity


Role = Literal["editor", "designer", "custodian", "hero", "designer_hero"]


class Account(BaseModel):
    email: str
    role: str
    source: str
    owner: bool
    added_by: str | None
    added_at: datetime | None
    note: str | None
    # What the account can do now (checkbox ids, app/core/permissions.py); ``custom`` is True when the
    # owner ticked them by hand, False when they are just what the role gives.
    permissions: list[str]
    custom: bool
    # A fixed account that already has a row here, so its permissions can be edited.
    imported: bool


class AccountList(BaseModel):
    data: list[Account]
    # The checkboxes in order, and what each role (a shortcut) gives.
    catalog: list[str]
    presets: dict[str, list[str]]
    # "synced" | "not_configured" | an error text, for the last change;
    # "unchanged" on a plain read.
    cloudflare: str
    sync_configured: bool


class AddBody(BaseModel):
    email: str = Field(max_length=254)
    role: Role
    note: str | None = Field(default=None, max_length=200)


class RoleBody(BaseModel):
    role: Role


class PermissionsBody(BaseModel):
    # None goes back to what the account's role gives.
    permissions: list[str] | None


reads = APIRouter(prefix="/api/admin/v1", tags=["admin", "accounts"], dependencies=[Depends(require_owner)])
writes = APIRouter(prefix="/api/admin/v1", tags=["admin", "accounts"],
                   dependencies=[Depends(require_owner), Depends(require_write_guard)])


def _list(db: Session, cloudflare: str) -> AccountList:
    return AccountList(data=[Account(**{**vars(a), "permissions": list(a.permissions)}) for a in admin_accounts.listing(db)],
                       catalog=list(perms.ALL_PERMISSIONS),
                       presets={r: [p for p in perms.ALL_PERMISSIONS if p in perms.for_roles({r})]
                                for r in ("editor", "designer", "custodian", "hero", "designer_hero")},
                       cloudflare=cloudflare, sync_configured=get_settings().access_sync_enabled)


def _change(db: Session, call) -> AccountList:
    try:
        call()
    except ContentError as exc:
        raise _fail(exc) from None
    return _list(db, admin_accounts.sync(db))


@reads.get("/accounts", response_model=AccountList)
def list_accounts(db: Session = Depends(get_db)) -> AccountList:
    return _list(db, "unchanged")


@writes.post("/accounts", response_model=AccountList)
def add_account(body: AddBody, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AccountList:
    return _change(db, lambda: admin_accounts.add(db, who, body.email, body.role, (body.note or "").strip() or None))


@writes.post("/accounts/sync", response_model=AccountList)
def sync_accounts(db: Session = Depends(get_db)) -> AccountList:
    return _list(db, admin_accounts.sync(db))


@writes.post("/accounts/{email}/role", response_model=AccountList)
def change_role(email: str, body: RoleBody, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AccountList:
    return _change(db, lambda: admin_accounts.change_role(db, who, email, body.role))


@writes.post("/accounts/{email}/remove", response_model=AccountList)
def remove_account(email: str, who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AccountList:
    return _change(db, lambda: admin_accounts.remove(db, who, email))


@writes.post("/accounts/import-fixed", response_model=AccountList)
def import_fixed_accounts(who: Actor = Depends(actor), db: Session = Depends(get_db)) -> AccountList:
    try:
        admin_accounts.import_fixed(db, who)
    except ContentError as exc:
        raise _fail(exc) from None
    return _list(db, "unchanged")


@writes.post("/accounts/{email}/permissions", response_model=AccountList)
def set_permissions(email: str, body: PermissionsBody, who: Actor = Depends(actor),
                    db: Session = Depends(get_db)) -> AccountList:
    try:
        admin_accounts.set_permissions(db, who, email, body.permissions)
    except ContentError as exc:
        raise _fail(exc) from None
    return _list(db, "unchanged")
