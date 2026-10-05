"""artisan authorization: the artisan can ask for changes (P-026 G3)

Revision ID: b7e2c5a9d41f
Revises: c4d1a7e2f9b3
Create Date: 2026-10-05 06:30:00.000000

The authorization link gets a third answer next to "Sí, autorizo" and
"No autorizo": "Quiero cambios", with the artisan's comment. It closes the
request like a decline does, but nothing is unpublished: the team corrects
the record and sends a new link.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7e2c5a9d41f'
down_revision: Union[str, None] = 'c4d1a7e2f9b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL >= 12 accepts this inside the migration's transaction; the
    # value is not used in the same transaction.
    op.execute("ALTER TYPE artisan_authorization_status ADD VALUE IF NOT EXISTS 'changes_requested'")


def downgrade() -> None:
    # An enum value cannot be dropped: rebuild the type without it. A request
    # for changes was a closed "not yet", the closest old value is declined.
    # The partial unique index depends on the type, so it is rebuilt too.
    op.execute("UPDATE artisan_authorization SET status = 'declined' WHERE status = 'changes_requested'")
    op.execute("DROP INDEX uq_artisan_authorization_one_open")
    op.execute("ALTER TYPE artisan_authorization_status RENAME TO artisan_authorization_status_old")
    op.execute("CREATE TYPE artisan_authorization_status AS ENUM ('pending', 'authorized', 'declined', 'revoked')")
    op.execute("ALTER TABLE artisan_authorization ALTER COLUMN status TYPE artisan_authorization_status "
               "USING status::text::artisan_authorization_status")
    op.execute("DROP TYPE artisan_authorization_status_old")
    op.execute("CREATE UNIQUE INDEX uq_artisan_authorization_one_open ON artisan_authorization (artisan_id) "
               "WHERE status IN ('pending', 'authorized')")
