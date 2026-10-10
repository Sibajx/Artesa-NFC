"""permission checkboxes per account: admin_account.permissions

Revision ID: f4a8c2e6d1b3
Revises: c8f1d2a7b9e4
Create Date: 2026-10-10 15:00:00.000000

Additive: a nullable column. NULL means "derive the permissions from the
role", so nobody's access changes when this is applied.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f4a8c2e6d1b3'
down_revision: Union[str, None] = 'c8f1d2a7b9e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('admin_account', sa.Column('permissions', sa.ARRAY(sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('admin_account', 'permissions')
