"""P-026 G4: Gestión accounts, managed by the owner.

The owner (OWNER_EMAILS) and everyone in ADMIN_EMAILS are fixed in
shared/.env and appear in the list as such; the owner adds, changes and
removes the rest here, without a restart. Every change is audited and, when
configured, mirrored into the Cloudflare Access group (cloudflare_access).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.access import ASSIGNABLE_ROLES, CUSTODIAN, DESIGNER, DESIGNER_HERO, EDITOR, HERO, OWNER
from app.core import permissions
from app.core.config import get_settings
from app.models.admin_account import AdminAccount
from app.models.audit_event import AuditActorType, AuditEvent, AuditResult
from app.services import cloudflare_access
from app.services.content import Actor, ContentConflict, ContentNotFound

EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}")
_ACCOUNTS_ENTITY = uuid.UUID(int=0)
_ROLE_SETS = {EDITOR: frozenset({EDITOR}), DESIGNER: frozenset({EDITOR, DESIGNER}),
              CUSTODIAN: frozenset({EDITOR, DESIGNER, CUSTODIAN}), HERO: frozenset({EDITOR, HERO}),
              DESIGNER_HERO: frozenset({EDITOR, DESIGNER, HERO})}


def roles_for(db: Session, email: str) -> frozenset[str] | None:
    """The roles of an active managed account, or None."""
    account = db.get(AdminAccount, email)
    return _ROLE_SETS.get(account.role) if account is not None and account.active else None


def permissions_for(db: Session, email: str) -> frozenset[str] | None:
    """The explicit permissions of an active managed account, or None to derive them from its role."""
    account = db.get(AdminAccount, email)
    if account is None or not account.active or account.permissions is None:
        return None
    return permissions.clean(account.permissions)


@dataclass(frozen=True)
class AccountView:
    email: str
    role: str
    source: str  # "configuracion" (shared/.env, fixed) | "gestion"
    owner: bool
    added_by: str | None
    added_at: datetime | None
    note: str | None
    # What the account can do now, and whether that is a custom list (True)
    # or just what its role gives (False).
    permissions: tuple[str, ...] = ()
    custom: bool = False
    # A fixed account (shared/.env) that already has a row in Gestión, so the
    # owner can narrow its permissions. Not removable from here.
    imported: bool = False


def _env_role(email: str) -> str:
    s = get_settings()
    if email in s.owner_emails_list or email in s.custodian_emails_list:
        return CUSTODIAN
    if email in s.designer_emails_list:
        return DESIGNER
    return EDITOR


def _env_roles(email: str) -> frozenset[str]:
    s = get_settings()
    role = _env_role(email)
    return {CUSTODIAN: frozenset({EDITOR, DESIGNER, CUSTODIAN}), DESIGNER: frozenset({EDITOR, DESIGNER}),
            EDITOR: frozenset({EDITOR})}[role] | ({OWNER} if email in s.owner_emails_list else frozenset())


def _effective(roles: frozenset[str], explicit: list[str] | None) -> tuple[tuple[str, ...], bool]:
    if OWNER in roles:
        return tuple(permissions.ALL_PERMISSIONS), False
    if explicit is not None:
        granted = permissions.clean(explicit)
        return tuple(p for p in permissions.ALL_PERMISSIONS if p in granted), True
    derived = permissions.for_roles(roles)
    return tuple(p for p in permissions.ALL_PERMISSIONS if p in derived), False


def listing(db: Session) -> list[AccountView]:
    s = get_settings()
    rows = {a.email: a for a in db.execute(select(AdminAccount).where(AdminAccount.active.is_(True))
                                           .order_by(AdminAccount.added_at)).scalars()}
    fixed = []
    for e in s.admin_emails_list:
        perms, custom = _effective(_env_roles(e), rows[e].permissions if e in rows else None)
        fixed.append(AccountView(email=e, role=_env_role(e), source="configuracion", owner=e in s.owner_emails_list,
                                 added_by=None, added_at=None, note=None, permissions=perms, custom=custom,
                                 imported=e in rows and e not in s.owner_emails_list))
    managed = []
    for a in rows.values():
        if a.email in s.admin_emails_list:
            continue
        perms, custom = _effective(_ROLE_SETS.get(a.role, frozenset({EDITOR})), a.permissions)
        managed.append(AccountView(email=a.email, role=a.role, source="gestion", owner=False, added_by=a.added_by,
                                   added_at=a.added_at, note=a.note, permissions=perms, custom=custom))
    return sorted(fixed, key=lambda a: (not a.owner, a.email)) + managed


def _audit(db: Session, actor: Actor, action: str, metadata: dict) -> None:
    db.add(AuditEvent(occurred_at=func.clock_timestamp(), actor_type=AuditActorType.admin_user,
                      actor_email=actor.identity.email, entity_type="admin_account", entity_id=_ACCOUNTS_ENTITY,
                      action=f"account.{action}", result=AuditResult.success, ip_address=actor.ip_address,
                      event_metadata=metadata))


def _clean(email: str, role: str) -> tuple[str, str]:
    email = email.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise ContentConflict("invalid_email", "Escribe un correo válido.", "email")
    if role not in ASSIGNABLE_ROLES:
        raise ContentConflict("invalid_role", "Rol desconocido.", "role")
    return email, role


def _fixed(email: str) -> bool:
    return email in get_settings().admin_emails_list


def add(db: Session, actor: Actor, email: str, role: str, note: str | None) -> None:
    email, role = _clean(email, role)
    if _fixed(email):
        raise ContentConflict("fixed_account", "Esta cuenta está fija en la configuración del servidor.")
    account = db.execute(select(AdminAccount).where(AdminAccount.email == email).with_for_update()).scalar_one_or_none()
    if account is not None and account.active:
        raise ContentConflict("account_exists", "Esa cuenta ya tiene acceso.")
    now = datetime.now(timezone.utc)
    if account is None:
        db.add(AdminAccount(email=email, role=role, active=True, note=note, added_by=actor.identity.email,
                            added_at=now, updated_at=now))
    else:
        account.role, account.active, account.note = role, True, note
        account.added_by, account.added_at, account.updated_at = actor.identity.email, now, now
        account.removed_by = account.removed_at = None
    _audit(db, actor, "added", {"email": email, "role": role})
    db.commit()


def change_role(db: Session, actor: Actor, email: str, role: str) -> None:
    email, role = _clean(email, role)
    if _fixed(email):
        raise ContentConflict("fixed_account", "Esta cuenta está fija en la configuración del servidor.")
    account = db.execute(select(AdminAccount).where(AdminAccount.email == email, AdminAccount.active.is_(True))
                         .with_for_update()).scalar_one_or_none()
    if account is None:
        raise ContentNotFound("not_found", "Esa cuenta no existe.")
    before, account.role, account.updated_at = account.role, role, datetime.now(timezone.utc)
    # A role is a shortcut: picking one drops any custom list and goes back to what the role gives.
    account.permissions = None
    _audit(db, actor, "role_changed", {"email": email, "from": before, "to": role})
    db.commit()


def set_permissions(db: Session, actor: Actor, email: str, requested: list[str] | None) -> None:
    """Sets the account's permission checkboxes; None goes back to what its role gives.
    The owner's permissions are fixed (all of them)."""
    email = email.strip().lower()
    if email in get_settings().owner_emails_list:
        raise ContentConflict("owner_account", "El dueño siempre tiene todos los permisos.")
    account = db.execute(select(AdminAccount).where(AdminAccount.email == email, AdminAccount.active.is_(True))
                         .with_for_update()).scalar_one_or_none()
    if account is None:
        raise ContentConflict("not_imported", "Importa primero las cuentas fijas a Gestión.") \
            if _fixed(email) else ContentNotFound("not_found", "Esa cuenta no existe.")
    if requested is not None:
        unknown = [p for p in requested if p not in permissions.ALL_PERMISSIONS]
        if unknown:
            raise ContentConflict("invalid_permission", "Permiso desconocido.", "permissions")
        if permissions.VIEW not in requested:
            raise ContentConflict("view_required", "Toda cuenta con acceso debe poder ver.", "permissions")
    before = account.permissions
    account.permissions = None if requested is None else [p for p in permissions.ALL_PERMISSIONS if p in requested]
    account.updated_at = datetime.now(timezone.utc)
    _audit(db, actor, "permissions_changed", {"email": email, "from": before, "to": account.permissions})
    db.commit()


def import_fixed(db: Session, actor: Actor) -> int:
    """Creates a row in Gestión for every fixed account except the owner, with the permissions it has now
    (so access does not change), so the owner can edit them from the matrix. Returns how many were created."""
    s = get_settings()
    now = datetime.now(timezone.utc)
    created = 0
    for email in s.admin_emails_list:
        if email in s.owner_emails_list:
            continue
        account = db.execute(select(AdminAccount).where(AdminAccount.email == email).with_for_update()).scalar_one_or_none()
        if account is not None and account.active:
            continue
        current = [p for p in permissions.ALL_PERMISSIONS if p in permissions.for_roles(_env_roles(email))]
        if account is None:
            db.add(AdminAccount(email=email, role=_env_role(email), active=True, permissions=current,
                                note="Importada de la configuración del servidor", added_by=actor.identity.email,
                                added_at=now, updated_at=now))
        else:
            account.role, account.active, account.permissions = _env_role(email), True, current
            account.added_by, account.added_at, account.updated_at = actor.identity.email, now, now
            account.removed_by = account.removed_at = None
        _audit(db, actor, "imported", {"email": email, "permissions": current})
        created += 1
    db.commit()
    return created


def remove(db: Session, actor: Actor, email: str) -> None:
    email = email.strip().lower()
    if _fixed(email):
        raise ContentConflict("fixed_account", "Esta cuenta está fija en la configuración del servidor.")
    account = db.execute(select(AdminAccount).where(AdminAccount.email == email, AdminAccount.active.is_(True))
                         .with_for_update()).scalar_one_or_none()
    if account is None:
        raise ContentNotFound("not_found", "Esa cuenta no existe.")
    now = datetime.now(timezone.utc)
    account.active, account.removed_by, account.removed_at, account.updated_at = False, actor.identity.email, now, now
    _audit(db, actor, "removed", {"email": email, "role": account.role})
    db.commit()


def sync(db: Session) -> str:
    """Mirrors everyone with access into the Cloudflare Access group.
    Returns "synced", "not_configured" or an error text (never raises)."""
    s = get_settings()
    if not s.access_sync_enabled:
        return "not_configured"
    emails = set(s.admin_emails_list) | {
        a.email for a in db.execute(select(AdminAccount).where(AdminAccount.active.is_(True))).scalars()}
    try:
        cloudflare_access.sync_group(token=s.access_sync_api_token, account_id=s.access_sync_account_id,
                                     group_id=s.access_sync_group_id, emails=emails)
    except cloudflare_access.SyncError as exc:
        return str(exc)
    return "synced"
