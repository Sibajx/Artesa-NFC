"""create append-only audit_event table

Revision ID: 904d7f9d6509
Revises: 895974720462
Create Date: 2026-09-28 23:00:37.412522

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '904d7f9d6509'
down_revision: Union[str, None] = '895974720462'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# DATA_MODEL.md section 2.6: audit_event is append-only. It is enforced in the
# database, not only by convention: UPDATE, DELETE and TRUNCATE raise for every
# role, the application role included. Rewriting history needs the table
# owner or a superuser to run an explicit ALTER TABLE ... DISABLE TRIGGER.
_APPEND_ONLY_FUNCTION = """
CREATE FUNCTION audit_event_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def upgrade() -> None:
    op.create_table('audit_event',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actor_type', sa.Enum('admin_user', 'system', name='audit_actor_type'), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('actor_email', sa.Text(), nullable=True),
    sa.Column('entity_type', sa.Text(), nullable=False),
    sa.Column('entity_id', sa.UUID(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('result', sa.Enum('success', 'failure', name='audit_result'), nullable=False),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_event_entity', 'audit_event', ['entity_type', 'entity_id'], unique=False)
    op.create_index('ix_audit_event_occurred_at', 'audit_event', ['occurred_at'], unique=False)

    op.execute(_APPEND_ONLY_FUNCTION)
    op.execute(
        "CREATE TRIGGER audit_event_no_update_delete BEFORE UPDATE OR DELETE ON audit_event "
        "FOR EACH ROW EXECUTE FUNCTION audit_event_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_event_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_truncate ON audit_event")
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_update_delete ON audit_event")
    op.execute("DROP FUNCTION IF EXISTS audit_event_append_only()")
    op.drop_index('ix_audit_event_occurred_at', table_name='audit_event')
    op.drop_index('ix_audit_event_entity', table_name='audit_event')
    op.drop_table('audit_event')
    postgresql.ENUM(name='audit_result').drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='audit_actor_type').drop(op.get_bind(), checkfirst=True)
