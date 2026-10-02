"""add trashed_at to artisan and piece (Gestión papelera)

Revision ID: a7c3e91d2b40
Revises: 904d7f9d6509
Create Date: 2026-10-02 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7c3e91d2b40'
down_revision: Union[str, None] = '904d7f9d6509'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('artisan', sa.Column('trashed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('piece', sa.Column('trashed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('piece', 'trashed_at')
    op.drop_column('artisan', 'trashed_at')
