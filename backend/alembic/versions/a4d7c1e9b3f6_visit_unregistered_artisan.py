"""visit: artisan not registered

Revision ID: a4d7c1e9b3f6
Revises: e5c2a8f1b7d3
Create Date: 2026-10-12 08:00:00.000000

Additive: one nullable column on visit (the name of an artisan who is not
registered yet) and two checks that only the new column can break. Nothing
existing changes and no row is touched; older code ignores the column, so a
code-only rollback is compatible.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a4d7c1e9b3f6'
down_revision: Union[str, None] = 'e5c2a8f1b7d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('visit', sa.Column('unregistered_artisan', sa.Text(), nullable=True))
    op.create_check_constraint(
        'ck_visit_artisan_exclusive', 'visit', 'artisan_id IS NULL OR unregistered_artisan IS NULL')
    op.create_check_constraint(
        'ck_visit_unregistered_name', 'visit',
        'unregistered_artisan IS NULL OR length(btrim(unregistered_artisan)) >= 2')


def downgrade() -> None:
    op.drop_constraint('ck_visit_unregistered_name', 'visit', type_='check')
    op.drop_constraint('ck_visit_artisan_exclusive', 'visit', type_='check')
    op.drop_column('visit', 'unregistered_artisan')
