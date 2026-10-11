"""supplies stock: supply and supply_movement

Revision ID: a7d3e1b5c9f2
Revises: f4a8c2e6d1b3
Create Date: 2026-10-10 18:00:00.000000

Additive: two new tables. Nothing existing changes and no row is seeded; older
code ignores them, so a code-only rollback is compatible.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7d3e1b5c9f2'
down_revision: Union[str, None] = 'f4a8c2e6d1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('supply',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('unit', sa.Text(), nullable=False),
    sa.Column('min_stock', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('min_stock >= 0', name='ck_supply_min_stock'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_supply_name_lower', 'supply', [sa.literal_column('lower(name)')], unique=True)
    op.create_table('supply_movement',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('supply_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('delta', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('unit_cost_cents', sa.Integer(), nullable=True),
    sa.Column('piece_id', postgresql.UUID(as_uuid=True), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('recorded_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(kind = 'purchase' AND delta > 0) OR (kind IN ('use', 'loss') AND delta < 0) OR (kind = 'adjustment' AND delta <> 0)", name='ck_supply_movement_delta'),
    sa.CheckConstraint("kind IN ('purchase', 'use', 'loss', 'adjustment')", name='ck_supply_movement_kind'),
    sa.CheckConstraint('unit_cost_cents IS NULL OR unit_cost_cents >= 0', name='ck_supply_movement_cost'),
    sa.ForeignKeyConstraint(['piece_id'], ['piece.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['supply_id'], ['supply.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_supply_movement_piece_id'), 'supply_movement', ['piece_id'], unique=False)
    op.create_index(op.f('ix_supply_movement_supply_id'), 'supply_movement', ['supply_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_supply_movement_supply_id'), table_name='supply_movement')
    op.drop_index(op.f('ix_supply_movement_piece_id'), table_name='supply_movement')
    op.drop_table('supply_movement')
    op.drop_index('uq_supply_name_lower', table_name='supply')
    op.drop_table('supply')
