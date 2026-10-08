"""piece location log (P-026 G12)

Revision ID: 57cc7fb123cb
Revises: e7a4c9b1d6f3
Additive (MIGRATION_DEPLOY): a new table only. Nothing existing changes, and
the previous code ignores it, so a rollback of the code alone is safe.
Create Date: 2026-10-07 23:34:56.265605

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57cc7fb123cb'
down_revision: Union[str, None] = 'e7a4c9b1d6f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('piece_location',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('piece_id', sa.UUID(), nullable=False),
    sa.Column('location', sa.Text(), nullable=False),
    sa.Column('place', sa.Text(), nullable=True),
    sa.Column('moved_on', sa.Date(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('recorded_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
    sa.CheckConstraint("location IN ('taller', 'bodega', 'tienda', 'exhibicion', 'transito', 'entregada', 'otro')", name='ck_piece_location_location'),
    sa.ForeignKeyConstraint(['piece_id'], ['piece.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_piece_location_piece_id'), 'piece_location', ['piece_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_piece_location_piece_id'), table_name='piece_location')
    op.drop_table('piece_location')
