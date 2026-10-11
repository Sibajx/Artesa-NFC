"""production follow-up: production_step and production_photo

Revision ID: b9e4f2a6d8c1
Revises: a7d3e1b5c9f2
Create Date: 2026-10-10 20:00:00.000000

Additive: two new tables. Nothing existing changes and no row is seeded; older
code ignores them, so a code-only rollback is compatible.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b9e4f2a6d8c1'
down_revision: Union[str, None] = 'a7d3e1b5c9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('production_step',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('piece_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('step', sa.Text(), nullable=False),
    sa.Column('done_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('done_by', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('carrier', sa.Text(), nullable=True),
    sa.Column('tracking', sa.Text(), nullable=True),
    sa.CheckConstraint("step IN ('received', 'chip_placed', 'packed', 'shipped', 'delivered')", name='ck_production_step_step'),
    sa.ForeignKeyConstraint(['piece_id'], ['piece.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('piece_id', 'step', name='uq_production_step_piece_step')
    )
    op.create_index(op.f('ix_production_step_piece_id'), 'production_step', ['piece_id'], unique=False)
    op.create_table('production_photo',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('step_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['step_id'], ['production_step.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_production_photo_step_id'), 'production_photo', ['step_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_production_photo_step_id'), table_name='production_photo')
    op.drop_table('production_photo')
    op.drop_index(op.f('ix_production_step_piece_id'), table_name='production_step')
    op.drop_table('production_step')
