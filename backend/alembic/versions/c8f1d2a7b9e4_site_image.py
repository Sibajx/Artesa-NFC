"""site images that Gestión can replace: site_image

Revision ID: c8f1d2a7b9e4
Revises: be517598c1d4
Create Date: 2026-10-10 09:30:00.000000

Additive. A slot without a row keeps the image built into the site.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c8f1d2a7b9e4'
down_revision: Union[str, None] = 'be517598c1d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('site_image',
    sa.Column('slot', sa.Text(), nullable=False),
    sa.Column('avif', sa.Text(), nullable=False),
    sa.Column('webp', sa.Text(), nullable=False),
    sa.Column('jpg', sa.Text(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('updated_by', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('slot')
    )


def downgrade() -> None:
    op.drop_table('site_image')
