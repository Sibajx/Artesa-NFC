"""visit log: visit

Revision ID: c1f7a3d9e5b2
Revises: b9e4f2a6d8c1
Create Date: 2026-10-10 21:00:00.000000

Additive: one new table. Nothing existing changes and no row is seeded; older
code ignores it, so a code-only rollback is compatible.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c1f7a3d9e5b2'
down_revision: Union[str, None] = 'b9e4f2a6d8c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('visit',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('visited_on', sa.Date(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('artisan_id', postgresql.UUID(as_uuid=True), nullable=True),
    sa.Column('place', sa.Text(), nullable=True),
    sa.Column('attendees', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('agreements', sa.Text(), nullable=True),
    sa.Column('consent_to_publish', sa.Boolean(), nullable=False),
    sa.Column('photo_path', sa.Text(), nullable=True),
    sa.Column('photo_width', sa.Integer(), nullable=True),
    sa.Column('photo_height', sa.Integer(), nullable=True),
    sa.Column('recorded_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(photo_path IS NULL) = (photo_width IS NULL)", name='ck_visit_photo_consistent'),
    sa.CheckConstraint("kind IN ('artesano', 'galeria', 'otro')", name='ck_visit_kind'),
    sa.CheckConstraint('length(btrim(summary)) >= 10', name='ck_visit_summary'),
    sa.ForeignKeyConstraint(['artisan_id'], ['artisan.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_visit_artisan_id'), 'visit', ['artisan_id'], unique=False)
    op.create_index(op.f('ix_visit_visited_on'), 'visit', ['visited_on'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_visit_visited_on'), table_name='visit')
    op.drop_index(op.f('ix_visit_artisan_id'), table_name='visit')
    op.drop_table('visit')
