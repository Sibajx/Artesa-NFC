"""hero by season: hero_campaign (P-028)

Revision ID: e7a4c9b1d6f3
Revises: d3f8a1c6e5b2
Create Date: 2026-10-09 20:00:00.000000

Additive. Seeds the three campaigns the PO decided: the default ("Hero
normal"), Día de Muertos (Oct 20 - Nov 2) and Navidad (Dec 16 - Dec 25).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7a4c9b1d6f3'
down_revision: Union[str, None] = 'd3f8a1c6e5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = op.create_table('hero_campaign',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('start_month', sa.Integer(), nullable=True),
    sa.Column('start_day', sa.Integer(), nullable=True),
    sa.Column('end_month', sa.Integer(), nullable=True),
    sa.Column('end_day', sa.Integer(), nullable=True),
    sa.Column('published', sa.Boolean(), nullable=False),
    sa.Column('forced', sa.Boolean(), nullable=False),
    sa.Column('forced_until', sa.Date(), nullable=True),
    sa.Column('processing_status', sa.Text(), nullable=False),
    sa.Column('processing_error', sa.Text(), nullable=True),
    sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('video_mp4', sa.Text(), nullable=True),
    sa.Column('video_webm', sa.Text(), nullable=True),
    sa.Column('poster', sa.Text(), nullable=True),
    sa.Column('updated_by', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("processing_status IN ('none', 'processing', 'ready', 'error')", name='ck_hero_campaign_processing_status'),
    sa.CheckConstraint('start_month BETWEEN 1 AND 12 AND end_month BETWEEN 1 AND 12 AND start_day BETWEEN 1 AND 31 AND end_day BETWEEN 1 AND 31', name='ck_hero_campaign_days'),
    sa.CheckConstraint('is_default = (start_month IS NULL)', name='ck_hero_campaign_default_has_no_range'),
    sa.CheckConstraint('(start_month IS NULL) = (end_month IS NULL) AND (start_month IS NULL) = (start_day IS NULL) AND (start_month IS NULL) = (end_day IS NULL)', name='ck_hero_campaign_range_complete'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_index('uq_hero_campaign_one_default', 'hero_campaign', ['is_default'], unique=True, postgresql_where=sa.text('is_default'))
    op.create_index('uq_hero_campaign_one_forced', 'hero_campaign', ['forced'], unique=True, postgresql_where=sa.text('forced'))
    base = dict(published=False, forced=False, processing_status='none', start_month=None, start_day=None,
                end_month=None, end_day=None)
    op.bulk_insert(table, [
        {**base, 'id': 'a1c0de00-0000-4000-8000-000000000001', 'slug': 'hero-normal', 'name': 'Hero normal', 'is_default': True},
        {**base, 'id': 'a1c0de00-0000-4000-8000-000000000002', 'slug': 'dia-de-muertos', 'name': 'Día de Muertos', 'is_default': False,
         'start_month': 10, 'start_day': 20, 'end_month': 11, 'end_day': 2},
        {**base, 'id': 'a1c0de00-0000-4000-8000-000000000003', 'slug': 'navidad', 'name': 'Navidad', 'is_default': False,
         'start_month': 12, 'start_day': 16, 'end_month': 12, 'end_day': 25},
    ])


def downgrade() -> None:
    op.drop_index('uq_hero_campaign_one_forced', table_name='hero_campaign')
    op.drop_index('uq_hero_campaign_one_default', table_name='hero_campaign')
    op.drop_table('hero_campaign')
