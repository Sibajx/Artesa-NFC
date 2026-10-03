"""ownership card, piece claim and stolen flag (ADR-030 phase 3)

Revision ID: 91fd01294c1c
Revises: a7c3e91d2b40
Create Date: 2026-10-03 12:00:21.401542

Additive (MIGRATION_DEPLOY): two new tables and one nullable column.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '91fd01294c1c'
down_revision: Union[str, None] = 'a7c3e91d2b40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ownership_card',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('piece_id', sa.UUID(), nullable=False),
    sa.Column('key_hash', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('active', 'blocked', 'replaced', name='ownership_card_status'), nullable=False),
    sa.Column('failed_attempts', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('blocked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('replaced_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'replaced') = (replaced_at IS NOT NULL)", name='ck_ownership_card_replaced_at_matches_status'),
    sa.CheckConstraint('failed_attempts >= 0', name='ck_ownership_card_failed_attempts'),
    sa.ForeignKeyConstraint(['piece_id'], ['piece.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ownership_card_piece_id'), 'ownership_card', ['piece_id'], unique=False)
    op.create_index('uq_ownership_card_one_current_per_piece', 'ownership_card', ['piece_id'], unique=True, postgresql_where=sa.text("status IN ('active', 'blocked')"))
    op.create_table('piece_claim',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('piece_id', sa.UUID(), nullable=False),
    sa.Column('owner_email', sa.Text(), nullable=False),
    sa.Column('pin_hash', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('active', 'released', name='piece_claim_status'), nullable=False),
    sa.Column('claimed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'released') = (released_at IS NOT NULL)", name='ck_piece_claim_released_at_matches_status'),
    sa.ForeignKeyConstraint(['piece_id'], ['piece.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_piece_claim_piece_id'), 'piece_claim', ['piece_id'], unique=False)
    op.create_index('uq_piece_claim_one_active_per_piece', 'piece_claim', ['piece_id'], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.add_column('piece', sa.Column('reported_stolen_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('piece', 'reported_stolen_at')
    op.drop_index('uq_piece_claim_one_active_per_piece', table_name='piece_claim', postgresql_where=sa.text("status = 'active'"))
    op.drop_index(op.f('ix_piece_claim_piece_id'), table_name='piece_claim')
    op.drop_table('piece_claim')
    op.drop_index('uq_ownership_card_one_current_per_piece', table_name='ownership_card', postgresql_where=sa.text("status IN ('active', 'blocked')"))
    op.drop_index(op.f('ix_ownership_card_piece_id'), table_name='ownership_card')
    op.drop_table('ownership_card')
    sa.Enum(name='piece_claim_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='ownership_card_status').drop(op.get_bind(), checkfirst=True)
