"""training manuals: training_manual and training_image

Revision ID: e5c2a8f1b7d3
Revises: d3a9b6e2f814
Create Date: 2026-10-11 12:00:00.000000

Additive: two new tables. Nothing existing changes and no row is seeded; older
code ignores them, so a code-only rollback is compatible.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5c2a8f1b7d3'
down_revision: Union[str, None] = 'd3a9b6e2f814'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('training_manual',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('craft', sa.Text(), nullable=False),
    sa.Column('written', sa.Text(), nullable=True),
    sa.Column('visual', sa.Text(), nullable=True),
    sa.Column('next_steps', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default='draft', nullable=False),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('reviewed_by', sa.Text(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'in_review', 'changes_requested', 'approved')", name='ck_training_manual_status'),
    sa.CheckConstraint('length(btrim(craft)) >= 2', name='ck_training_manual_craft'),
    sa.CheckConstraint('length(btrim(title)) >= 3', name='ck_training_manual_title'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_training_manual_craft'), 'training_manual', ['craft'], unique=False)
    op.create_table('training_image',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('manual_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('caption', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('width > 0 AND height > 0', name='ck_training_image_size'),
    sa.ForeignKeyConstraint(['manual_id'], ['training_manual.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_training_image_manual_id'), 'training_image', ['manual_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_training_image_manual_id'), table_name='training_image')
    op.drop_table('training_image')
    op.drop_index(op.f('ix_training_manual_craft'), table_name='training_manual')
    op.drop_table('training_manual')
