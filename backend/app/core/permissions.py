"""Permission checkboxes for Gestión (the owner's "Usuarios" matrix).

Twelve permissions (eleven agreed with the PO on 2026-10-10, plus Visits) (Artesa_Brain/02_Producto/
Equipo.md). Managing users is not one of them: only the owner does that
(``OWNER`` role, fixed in OWNER_EMAILS).

PR 1 is a no-op for access: every role still means what it meant, and the
permissions an account has are derived from its roles (``ROLE_PERMISSIONS``).
Only an account whose ``admin_account.permissions`` is set (PR 2/3) gets an
explicit list instead; the owner always has all of them.
"""
from __future__ import annotations

from collections.abc import Iterable

VIEW = "view"                    # 1 Ver
EDIT = "edit"                    # 2 Editar artesanos y piezas (fotos, paleta, papelera)
PUBLISH = "publish"              # 3 Publicar y archivar
AUTHORIZATION = "authorization"  # 4 Autorización del artesano por WhatsApp
SALES = "sales"                  # 5 Ventas
LOGISTICS = "logistics"          # 6 Logística y entregas (ubicación, estados de envío)
NFC = "nfc"                      # 7 Certificación NFC (chips y tarjetas)
REVOCATIONS = "revocations"      # 8 Revocaciones y robos
DESIGN = "design"                # 9 Diseño de certificados
HERO = "hero"                    # 10 Hero e imágenes del sitio
TRAINING = "training"            # 11 Capacitaciones (módulo aún sin construir)
VISITS = "visits"                # 12 Visitas y bitácora (solo se da a mano: Sol y Hariel)

ALL_PERMISSIONS: tuple[str, ...] = (
    VIEW, EDIT, PUBLISH, AUTHORIZATION, SALES, LOGISTICS, NFC, REVOCATIONS, DESIGN, HERO, TRAINING, VISITS,
)

# Role -> permissions, matching exactly what each role could do before the
# checkboxes existed: an editor could do everything outside design, custody
# and the hero; a designer adds design; a custodian (also a designer) adds
# custody; the hero manager adds the hero. TRAINING has no endpoints yet. VISITS
# (the visit log) is in no role: the owner gives it by hand, to Sol and Hariel.
_EDITOR = frozenset({VIEW, EDIT, PUBLISH, AUTHORIZATION, SALES, LOGISTICS})
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "editor": _EDITOR,
    "designer": _EDITOR | {DESIGN},
    "custodian": _EDITOR | {DESIGN, NFC, REVOCATIONS},
    "hero": _EDITOR | {HERO},
    "designer_hero": _EDITOR | {DESIGN, HERO},
    "owner": frozenset(ALL_PERMISSIONS),
}


def for_roles(roles: Iterable[str]) -> frozenset[str]:
    """The union of the permissions of every role."""
    granted: set[str] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(granted)


def clean(raw: Iterable[str]) -> frozenset[str]:
    """A stored/requested list, as a set of known permissions; unknown names are dropped."""
    return frozenset(p for p in raw if p in ALL_PERMISSIONS)
