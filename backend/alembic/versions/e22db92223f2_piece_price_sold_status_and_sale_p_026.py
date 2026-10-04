"""piece price, sold status and sale (P-026)

Revision ID: e22db92223f2
Revises: ea1e402ecf26
Additive (MIGRATION_DEPLOY): the sale table, piece.price_cents and
piece.price_currency, a check, and the 'sold' value of the availability enum.
Only the new code writes 'sold' (by registering a sale).

Create Date: 2026-10-04 15:26:26.536971

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e22db92223f2'
down_revision: Union[str, None] = 'ea1e402ecf26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('sale',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('piece_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Enum('active', 'cancelled', name='sale_status'), nullable=False),
    sa.Column('sold_on', sa.Date(), nullable=False),
    sa.Column('price_cents', sa.Integer(), nullable=False),
    sa.Column('currency', sa.Text(), nullable=False),
    sa.Column('channel', sa.Text(), nullable=False),
    sa.Column('sold_by', sa.Text(), nullable=False),
    sa.Column('buyer_name', sa.Text(), nullable=True),
    sa.Column('buyer_contact', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('recorded_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=True),
    sa.Column('cancelled_by', sa.Text(), nullable=True),
    sa.CheckConstraint("(status = 'cancelled') = (cancelled_at IS NOT NULL)", name='ck_sale_cancelled_at_matches_status'),
    sa.CheckConstraint("channel IN ('taller', 'tienda', 'en_linea', 'feria', 'otro')", name='ck_sale_channel'),
    sa.CheckConstraint('price_cents >= 0', name='ck_sale_price_cents'),
    sa.ForeignKeyConstraint(['piece_id'], ['piece.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sale_piece_id'), 'sale', ['piece_id'], unique=False)
    op.create_index('uq_sale_one_active_per_piece', 'sale', ['piece_id'], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.add_column('piece', sa.Column('price_cents', sa.Integer(), nullable=True))
    op.add_column('piece', sa.Column('price_currency', sa.Text(), server_default='MXN', nullable=False))
    op.create_check_constraint('ck_piece_price_cents', 'piece', 'price_cents IS NULL OR price_cents >= 0')
    # PostgreSQL >= 12 accepts this inside the migration's transaction; the
    # value is not used in the same transaction.
    op.execute("ALTER TYPE piece_availability_status ADD VALUE IF NOT EXISTS 'sold'")


def downgrade() -> None:
    # An enum value cannot be dropped: rebuild the type without 'sold'.
    op.execute("UPDATE piece SET availability_status = 'available' WHERE availability_status = 'sold'")
    op.execute("ALTER TYPE piece_availability_status RENAME TO piece_availability_status_old")
    op.execute("CREATE TYPE piece_availability_status AS ENUM ('available', 'reserved', 'exhibited', 'archived')")
    op.execute("ALTER TABLE piece ALTER COLUMN availability_status TYPE piece_availability_status "
               "USING availability_status::text::piece_availability_status")
    op.execute("DROP TYPE piece_availability_status_old")
    op.drop_constraint('ck_piece_price_cents', 'piece', type_='check')
    op.drop_column('piece', 'price_currency')
    op.drop_column('piece', 'price_cents')
    op.drop_index('uq_sale_one_active_per_piece', table_name='sale', postgresql_where=sa.text("status = 'active'"))
    op.drop_index(op.f('ix_sale_piece_id'), table_name='sale')
    op.drop_table('sale')
    sa.Enum(name='sale_status').drop(op.get_bind(), checkfirst=True)
