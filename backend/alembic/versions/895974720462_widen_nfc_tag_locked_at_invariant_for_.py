"""widen nfc_tag locked_at invariant for historical preservation

Revision ID: 895974720462
Revises: 9b8bb430770d
Create Date: 2026-09-18 15:43:06.467767

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '895974720462'
down_revision: Union[str, None] = '9b8bb430770d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Issue #70: locked_at must be preserved as historical metadata when a
    # locked tag later moves to replaced/retired, not cleared. The original
    # constraint only allowed locked_at while status = 'locked'.
    op.drop_constraint("ck_nfc_tag_locked_at_matches_status", "nfc_tag", type_="check")
    op.create_check_constraint(
        "ck_nfc_tag_locked_at_matches_status",
        "nfc_tag",
        "locked_at IS NULL OR status IN ('locked', 'replaced', 'retired')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_nfc_tag_locked_at_matches_status", "nfc_tag", type_="check")
    op.create_check_constraint(
        "ck_nfc_tag_locked_at_matches_status",
        "nfc_tag",
        "locked_at IS NULL OR status = 'locked'",
    )
