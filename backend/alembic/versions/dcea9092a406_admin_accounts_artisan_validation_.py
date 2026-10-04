"""admin accounts, artisan validation contact and authorization (P-026)

Revision ID: dcea9092a406
Revises: e22db92223f2
Additive (MIGRATION_DEPLOY): admin_account and artisan_authorization tables and
two nullable artisan columns. Older code never reads or writes them.

Create Date: 2026-10-04 15:47:05.553600

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'dcea9092a406'
down_revision: Union[str, None] = 'e22db92223f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('admin_account',
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('added_by', sa.Text(), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('removed_by', sa.Text(), nullable=True),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('email = lower(email)', name='ck_admin_account_email_lower'),
    sa.PrimaryKeyConstraint('email')
    )
    op.create_table('artisan_authorization',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('artisan_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'authorized', 'declined', 'revoked', name='artisan_authorization_status'), nullable=False),
    sa.Column('medium', sa.Text(), nullable=False),
    sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('requested_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_ip', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['artisan_id'], ['artisan.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_artisan_authorization_artisan_id'), 'artisan_authorization', ['artisan_id'], unique=False)
    op.create_index('uq_artisan_authorization_one_open', 'artisan_authorization', ['artisan_id'], unique=True, postgresql_where=sa.text("status IN ('pending', 'authorized')"))
    op.add_column('artisan', sa.Column('validation_whatsapp', sa.Text(), nullable=True))
    op.add_column('artisan', sa.Column('validation_contact_name', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('artisan', 'validation_contact_name')
    op.drop_column('artisan', 'validation_whatsapp')
    op.drop_index('uq_artisan_authorization_one_open', table_name='artisan_authorization', postgresql_where=sa.text("status IN ('pending', 'authorized')"))
    op.drop_index(op.f('ix_artisan_authorization_artisan_id'), table_name='artisan_authorization')
    op.drop_table('artisan_authorization')
    sa.Enum(name='artisan_authorization_status').drop(op.get_bind(), checkfirst=True)
    op.drop_table('admin_account')
