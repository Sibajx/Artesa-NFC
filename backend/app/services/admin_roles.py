"""Role profiles: named sets of permission checkboxes, edited by the owner.

A profile is a shortcut, not a link: picking one in "Usuarios" copies its
checkboxes to the account, and the profile shown for an account is whichever
has exactly the same set. Editing or deleting a profile never changes anyone's
access.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import permissions
from app.models.admin_role_profile import AdminRoleProfile
from app.models.audit_event import AuditActorType, AuditEvent, AuditResult
from app.services.content import Actor, ContentConflict, ContentNotFound

_ENTITY = uuid.UUID(int=0)
MAX_PROFILES = 20


def _slug(name: str) -> str:
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "rol"


def listing(db: Session) -> list[AdminRoleProfile]:
    return list(db.execute(select(AdminRoleProfile).order_by(AdminRoleProfile.position, AdminRoleProfile.name)).scalars())


def matching(profiles: list[AdminRoleProfile], granted: set[str] | frozenset[str]) -> str | None:
    """The name of the profile whose checkboxes are exactly ``granted``, if any."""
    for profile in profiles:
        if set(permissions.clean(profile.permissions)) == set(granted):
            return profile.name
    return None


def _audit(db: Session, actor: Actor, action: str, metadata: dict) -> None:
    db.add(AuditEvent(occurred_at=func.clock_timestamp(), actor_type=AuditActorType.admin_user,
                      actor_email=actor.identity.email, entity_type="admin_role", entity_id=_ENTITY,
                      action=f"role.{action}", result=AuditResult.success, ip_address=actor.ip_address,
                      event_metadata=metadata))


def _checked(name: str, description: str | None, requested: list[str]) -> tuple[str, str | None, list[str]]:
    name = " ".join(name.split())
    if not 2 <= len(name) <= 40:
        raise ContentConflict("invalid_name", "El nombre debe tener entre 2 y 40 caracteres.", "name")
    if any(p not in permissions.ALL_PERMISSIONS for p in requested):
        raise ContentConflict("invalid_permission", "Permiso desconocido.", "permissions")
    if permissions.VIEW not in requested:
        raise ContentConflict("view_required", "Todo rol debe poder ver.", "permissions")
    description = (description or "").strip() or None
    if description is not None and len(description) > 200:
        raise ContentConflict("invalid_description", "La descripción admite hasta 200 caracteres.", "description")
    return name, description, [p for p in permissions.ALL_PERMISSIONS if p in requested]


def _name_taken(db: Session, name: str, except_slug: str | None = None) -> bool:
    query = select(AdminRoleProfile.slug).where(func.lower(AdminRoleProfile.name) == name.lower())
    found = db.execute(query).scalars().first()
    return found is not None and found != except_slug


def create(db: Session, actor: Actor, name: str, description: str | None, requested: list[str]) -> None:
    name, description, perms = _checked(name, description, requested)
    if db.execute(select(func.count()).select_from(AdminRoleProfile)).scalar_one() >= MAX_PROFILES:
        raise ContentConflict("too_many_roles", f"Hay un máximo de {MAX_PROFILES} roles.")
    if _name_taken(db, name):
        raise ContentConflict("role_exists", "Ya hay un rol con ese nombre.", "name")
    slug, n = _slug(name), 1
    while db.get(AdminRoleProfile, slug) is not None:
        n += 1
        slug = f"{_slug(name)}-{n}"
    position = (db.execute(select(func.max(AdminRoleProfile.position))).scalar() or 0) + 1
    db.add(AdminRoleProfile(slug=slug, name=name, description=description, permissions=perms, position=position,
                            updated_by=actor.identity.email))
    _audit(db, actor, "created", {"slug": slug, "name": name, "permissions": perms})
    db.commit()


def update(db: Session, actor: Actor, slug: str, name: str, description: str | None, requested: list[str]) -> None:
    profile = db.execute(select(AdminRoleProfile).where(AdminRoleProfile.slug == slug).with_for_update()).scalar_one_or_none()
    if profile is None:
        raise ContentNotFound("not_found", "Ese rol no existe.")
    name, description, perms = _checked(name, description, requested)
    if _name_taken(db, name, slug):
        raise ContentConflict("role_exists", "Ya hay un rol con ese nombre.", "name")
    before = {"name": profile.name, "permissions": profile.permissions}
    profile.name, profile.description, profile.permissions = name, description, perms
    profile.updated_by, profile.updated_at = actor.identity.email, datetime.now(timezone.utc)
    _audit(db, actor, "updated", {"slug": slug, "from": before, "to": {"name": name, "permissions": perms}})
    db.commit()


def delete(db: Session, actor: Actor, slug: str) -> None:
    profile = db.execute(select(AdminRoleProfile).where(AdminRoleProfile.slug == slug).with_for_update()).scalar_one_or_none()
    if profile is None:
        raise ContentNotFound("not_found", "Ese rol no existe.")
    db.delete(profile)
    _audit(db, actor, "deleted", {"slug": slug, "name": profile.name})
    db.commit()
