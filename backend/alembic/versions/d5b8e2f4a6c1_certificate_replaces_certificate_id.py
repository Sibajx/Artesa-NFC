"""certificate.replaces_certificate_id: the explicit replacement chain (PEND-032)

Revision ID: d5b8e2f4a6c1
Revises: c3e9a5d7b1f2
Create Date: 2026-10-13 09:00:00.000000

Additive: one nullable column, its foreign key and an index. Nothing existing
changes and no row is rewritten (certificates replaced before this migration
keep it empty); older code ignores the column, so a code-only rollback is
compatible.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd5b8e2f4a6c1'
down_revision: Union[str, None] = 'c3e9a5d7b1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('certificate', sa.Column('replaces_certificate_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_certificate_replaces_certificate_id_certificate',
        'certificate', 'certificate', ['replaces_certificate_id'], ['id'], ondelete='RESTRICT',
    )
    op.create_check_constraint(
        'ck_certificate_replaces_not_self', 'certificate', 'replaces_certificate_id <> id',
    )
    op.create_index('ix_certificate_replaces_certificate_id', 'certificate', ['replaces_certificate_id'])


def downgrade() -> None:
    op.drop_index('ix_certificate_replaces_certificate_id', table_name='certificate')
    op.drop_constraint('ck_certificate_replaces_not_self', 'certificate', type_='check')
    op.drop_constraint('fk_certificate_replaces_certificate_id_certificate', 'certificate', type_='foreignkey')
    op.drop_column('certificate', 'replaces_certificate_id')
