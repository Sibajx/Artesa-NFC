"""schema invariants the application already enforces, now in the database (PEND-024)

Revision ID: e7c1f3a9d5b2
Revises: d5b8e2f4a6c1
Create Date: 2026-10-13 12:00:00.000000

The audit of 2026-10-13 listed every numeric column with no CHECK and one foreign
key with no index. Each constraint below only states what the code already
guarantees (versions start at 1, positions and image sizes are never negative
or zero, a year is a real year, a slug is not blank), so no application path
writes a row it would refuse. Production was checked read-only on 2026-10-13:
no existing row violates any of them, so every ADD CONSTRAINT validates at once.

Older code keeps working (it never wrote a violating value); a code-only
rollback is compatible. The downgrade drops them again.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7c1f3a9d5b2'
down_revision: Union[str, None] = 'd5b8e2f4a6c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECKS = (
    ("certificate", "ck_certificate_version", "version >= 1"),
    ("admin_role_profile", "ck_admin_role_profile_position", "position >= 0"),
    ("collection_entry", "ck_collection_entry_position", "position >= 0"),
    ("media_asset", "ck_media_asset_position", "position >= 0"),
    ("certificate_art", "ck_certificate_art_dimensions", "width > 0 AND height > 0"),
    ("production_photo", "ck_production_photo_dimensions", "width > 0 AND height > 0"),
    ("site_image", "ck_site_image_dimensions", "width > 0 AND height > 0"),
    ("visit", "ck_visit_photo_dimensions",
     "(photo_width IS NULL OR photo_width > 0) AND (photo_height IS NULL OR photo_height > 0)"),
    ("piece", "ck_piece_creation_year", "creation_year IS NULL OR creation_year BETWEEN 1 AND 9999"),
    ("piece", "ck_piece_slug_not_blank", "length(btrim(slug)) > 0"),
    ("artisan", "ck_artisan_slug_not_blank", "length(btrim(slug)) > 0"),
)


def upgrade() -> None:
    for table, name, expression in CHECKS:
        op.create_check_constraint(name, table, expression)
    op.create_index('ix_owner_verification_claim_id', 'owner_verification', ['claim_id'])


def downgrade() -> None:
    op.drop_index('ix_owner_verification_claim_id', table_name='owner_verification')
    for table, name, _ in reversed(CHECKS):
        op.drop_constraint(name, table, type_='check')
