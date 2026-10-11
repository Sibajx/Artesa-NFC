"""role profiles and the production checkbox: admin_role_profile

Revision ID: d3a9b6e2f814
Revises: c1f7a3d9e5b2
Create Date: 2026-10-11 10:00:00.000000

Additive: one new table with five seeded profiles, and the new 'production'
checkbox added to every explicit list that already has 'logistics' (the
production board used to be gated by Logística, so nobody loses access).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd3a9b6e2f814'
down_revision: Union[str, None] = 'c1f7a3d9e5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALL = ["view", "edit", "publish", "authorization", "sales", "logistics", "nfc", "revocations",
       "design", "hero", "training", "visits", "production"]
SEED = [
    ("administracion", "Administración", "Todo, menos gestionar usuarios (eso es solo del dueño).", ALL),
    ("certificador", "Certificador", "Autorización por WhatsApp, certificación NFC, revocaciones y diseño.",
     ["view", "authorization", "nfc", "revocations", "design"]),
    ("diseno-y-marca", "Diseño y Marca", "Catálogo, ventas, diseño, hero, visitas y producción.",
     ["view", "edit", "publish", "sales", "logistics", "design", "hero", "visits", "production"]),
    ("operaciones", "Operaciones", "Ventas, logística, producción, capacitaciones y visitas.",
     ["view", "sales", "logistics", "training", "visits", "production"]),
    ("consulta", "Consulta", "Solo ver, sin escribir nada.", ["view"]),
]


def upgrade() -> None:
    table = op.create_table('admin_role_profile',
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('permissions', sa.ARRAY(sa.Text()), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('updated_by', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("'view' = ANY(permissions)", name='ck_admin_role_profile_view'),
    sa.PrimaryKeyConstraint('slug'),
    sa.UniqueConstraint('name'))
    op.bulk_insert(table, [{"slug": s, "name": n, "description": d, "permissions": p, "position": i}
                           for i, (s, n, d, p) in enumerate(SEED)])
    op.execute("UPDATE admin_account SET permissions = array_append(permissions, 'production') "
               "WHERE permissions IS NOT NULL AND 'logistics' = ANY(permissions) AND NOT ('production' = ANY(permissions))")


def downgrade() -> None:
    op.execute("UPDATE admin_account SET permissions = array_remove(permissions, 'production') "
               "WHERE permissions IS NOT NULL")
    op.drop_table('admin_role_profile')
