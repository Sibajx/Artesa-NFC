"""visit photos: several private photos per visit

Revision ID: b6f2d8a4c1e9
Revises: a4d7c1e9b3f6
Create Date: 2026-10-12 21:00:00.000000

Additive: one new table, and the single photo each visit already had is copied
into it (the old visit.photo_* columns stay, unused, so a code-only rollback is
compatible: older code still shows the photo it had).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b6f2d8a4c1e9'
down_revision: Union[str, None] = 'a4d7c1e9b3f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BACKFILL_SQL = """
    INSERT INTO visit_photo (id, visit_id, path, width, height, created_by, created_at)
    SELECT gen_random_uuid(), id, photo_path, photo_width, photo_height, recorded_by, updated_at
    FROM visit WHERE photo_path IS NOT NULL
"""

# The oldest photo of each visit goes back to the single-photo columns.
RESTORE_SQL = """
    UPDATE visit SET photo_path = p.path, photo_width = p.width, photo_height = p.height
    FROM (SELECT DISTINCT ON (visit_id) visit_id, path, width, height
          FROM visit_photo ORDER BY visit_id, created_at, id) AS p
    WHERE visit.id = p.visit_id
"""


def upgrade() -> None:
    op.create_table('visit_photo',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('visit_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('width > 0 AND height > 0', name='ck_visit_photo_size'),
    sa.ForeignKeyConstraint(['visit_id'], ['visit.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_visit_photo_visit_id'), 'visit_photo', ['visit_id'], unique=False)
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.execute(RESTORE_SQL)
    op.drop_index(op.f('ix_visit_photo_visit_id'), table_name='visit_photo')
    op.drop_table('visit_photo')
