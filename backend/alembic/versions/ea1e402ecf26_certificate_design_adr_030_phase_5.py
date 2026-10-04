"""certificate_design (ADR-030 phase 5)

Revision ID: ea1e402ecf26
Revises: 91fd01294c1c
Additive (MIGRATION_DEPLOY): one new table.

Create Date: 2026-10-04 02:09:44.257686

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ea1e402ecf26'
down_revision: Union[str, None] = '91fd01294c1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('certificate_design',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('piece_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('draft', 'in_review', 'approved', 'published', 'superseded', name='certificate_design_status'), nullable=False),
    sa.Column('template', sa.Text(), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('review_token_hash', sa.Text(), nullable=True),
    sa.Column('review_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('change_request', sa.Text(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_by_name', sa.Text(), nullable=True),
    sa.Column('approval_medium', sa.Text(), nullable=True),
    sa.Column('approval_note', sa.Text(), nullable=True),
    sa.Column('approval_recorded_by', sa.Text(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('published_by', sa.Text(), nullable=True),
    sa.CheckConstraint("(status IN ('approved', 'published', 'superseded')) = (approved_at IS NOT NULL)", name='ck_certificate_design_approved_at_matches_status'),
    sa.CheckConstraint("(status IN ('published', 'superseded')) = (published_at IS NOT NULL)", name='ck_certificate_design_published_at_matches_status'),
    sa.CheckConstraint('version >= 1', name='ck_certificate_design_version'),
    sa.ForeignKeyConstraint(['piece_id'], ['piece.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('piece_id', 'version', name='uq_certificate_design_piece_version'),
    sa.UniqueConstraint('review_token_hash')
    )
    op.create_index(op.f('ix_certificate_design_piece_id'), 'certificate_design', ['piece_id'], unique=False)
    op.create_index('uq_certificate_design_one_open_per_piece', 'certificate_design', ['piece_id'], unique=True, postgresql_where=sa.text("status IN ('draft', 'in_review', 'approved')"))
    op.create_index('uq_certificate_design_one_published_per_piece', 'certificate_design', ['piece_id'], unique=True, postgresql_where=sa.text("status = 'published'"))


def downgrade() -> None:
    op.drop_index('uq_certificate_design_one_published_per_piece', table_name='certificate_design', postgresql_where=sa.text("status = 'published'"))
    op.drop_index('uq_certificate_design_one_open_per_piece', table_name='certificate_design', postgresql_where=sa.text("status IN ('draft', 'in_review', 'approved')"))
    op.drop_index(op.f('ix_certificate_design_piece_id'), table_name='certificate_design')
    op.drop_table('certificate_design')
    sa.Enum(name='certificate_design_status').drop(op.get_bind(), checkfirst=True)
