"""collection entries: the home's collection block, several entries

Revision ID: c3e9a5d7b1f2
Revises: b6f2d8a4c1e9
Create Date: 2026-10-12 22:00:00.000000

Additive: one new table. Nothing existing changes and no row is seeded; older
code ignores it, so a code-only rollback is compatible (the home keeps its
single built-in entry).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3e9a5d7b1f2'
down_revision: Union[str, None] = 'b6f2d8a4c1e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('collection_entry',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('cta_label', sa.Text(), nullable=True),
    sa.Column('link', sa.Text(), nullable=True),
    sa.Column('active', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('avif', sa.Text(), nullable=True),
    sa.Column('webp', sa.Text(), nullable=True),
    sa.Column('jpg', sa.Text(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('updated_by', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(btrim(title)) >= 3', name='ck_collection_entry_title'),
    sa.CheckConstraint('(avif IS NULL) = (jpg IS NULL) AND (webp IS NULL) = (jpg IS NULL) AND (width IS NULL) = (jpg IS NULL) AND (height IS NULL) = (jpg IS NULL)', name='ck_collection_entry_image_consistent'),
    sa.CheckConstraint('NOT active OR jpg IS NOT NULL', name='ck_collection_entry_active_has_image'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_collection_entry_position'), 'collection_entry', ['position'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_collection_entry_position'), table_name='collection_entry')
    op.drop_table('collection_entry')
