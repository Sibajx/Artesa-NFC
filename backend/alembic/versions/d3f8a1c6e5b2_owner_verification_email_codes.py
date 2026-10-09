"""owner verification: email codes for PIN reset and lost cards

Revision ID: d3f8a1c6e5b2
Revises: b7e2c5a9d41f
Create Date: 2026-10-08 16:00:00.000000

A one-time 6-digit code, emailed to the owner registered in the claim, proves
who asks when the PIN is forgotten or the card is lost or stolen. Only the
code's hash is stored.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd3f8a1c6e5b2'
down_revision: Union[str, None] = 'b7e2c5a9d41f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('owner_verification',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('piece_id', sa.UUID(), nullable=False),
    sa.Column('claim_id', sa.UUID(), nullable=False),
    sa.Column('purpose', sa.Enum('pin_reset', 'custody', name='owner_verification_purpose'), nullable=False),
    sa.Column('code_hash', sa.Text(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('attempts >= 0', name='ck_owner_verification_attempts'),
    sa.ForeignKeyConstraint(['claim_id'], ['piece_claim.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['piece_id'], ['piece.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_owner_verification_piece_id'), 'owner_verification', ['piece_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_owner_verification_piece_id'), table_name='owner_verification')
    op.drop_table('owner_verification')
    sa.Enum(name='owner_verification_purpose').drop(op.get_bind(), checkfirst=True)
