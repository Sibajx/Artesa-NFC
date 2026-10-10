"""certificate art (ADR-030 phase 5b)

Revision ID: be517598c1d4
Revises: 57cc7fb123cb
Additive (MIGRATION_DEPLOY): a new table only. Older code never reads it, and
a design that references art still renders under older code (without the art).
Create Date: 2026-10-07 23:55:26.785850

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'be517598c1d4'
down_revision: Union[str, None] = '57cc7fb123cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('certificate_art',
    sa.Column('sha256', sa.Text(), nullable=False),
    sa.Column('mime_type', sa.Text(), nullable=False),
    sa.Column('content', sa.LargeBinary(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('uploaded_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("mime_type IN ('image/png', 'image/jpeg')", name='ck_certificate_art_mime_type'),
    sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name='ck_certificate_art_sha256'),
    sa.PrimaryKeyConstraint('sha256')
    )


def downgrade() -> None:
    op.drop_table('certificate_art')
