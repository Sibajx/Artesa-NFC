"""pricing calculator: settings row and the artisan's price per piece

Revision ID: b6d2f8a4c1e9
Revises: e7c1f3a9d5b2
Create Date: 2026-11-01 19:20:00.000000

Additive only: a new single-row table and two nullable columns on piece.
Nothing existing changes meaning; without a settings row the defaults in
app/services/pricing.py apply.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b6d2f8a4c1e9'
down_revision: Union[str, None] = 'e7c1f3a9d5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pricing_settings',
        sa.Column('id', sa.SmallInteger(), nullable=False),
        sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_by', sa.Text(), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_pricing_settings_single_row'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.add_column('piece', sa.Column('artisan_price_cents', sa.Integer(), nullable=True))
    op.add_column('piece', sa.Column('commission_pct', sa.Numeric(precision=5, scale=2), nullable=True))
    op.create_check_constraint('ck_piece_artisan_price_cents', 'piece',
                               'artisan_price_cents IS NULL OR artisan_price_cents >= 0')
    op.create_check_constraint('ck_piece_commission_pct', 'piece',
                               'commission_pct IS NULL OR commission_pct BETWEEN 0 AND 100')


def downgrade() -> None:
    op.drop_constraint('ck_piece_commission_pct', 'piece', type_='check')
    op.drop_constraint('ck_piece_artisan_price_cents', 'piece', type_='check')
    op.drop_column('piece', 'commission_pct')
    op.drop_column('piece', 'artisan_price_cents')
    op.drop_table('pricing_settings')
