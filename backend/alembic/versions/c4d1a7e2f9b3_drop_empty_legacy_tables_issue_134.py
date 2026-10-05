"""drop the empty legacy tables (#134, Brain B-023)

Revision ID: c4d1a7e2f9b3
Revises: dcea9092a406
Create Date: 2026-10-05 04:30:00.000000

Production carries seven tables from an earlier prototype that no migration
created and no code reads: artesanas, prendas, tags_nfc, registros_lectura,
transacciones, usuarios, registros_contables. The backup manifest of
2026-10-04 (20261004T235814Z-14b3cf0ff994) counts 0 rows in each.

Guarded on purpose: a table that does not exist is skipped (every database
built from these migrations), and if ANY of them holds a row the migration
fails before dropping anything, so data is never lost by surprise. All seven
go in a single DROP TABLE, so foreign keys between them do not matter, and
there is no CASCADE: a dependency from a current table would make it fail.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d1a7e2f9b3'
down_revision: Union[str, None] = 'dcea9092a406'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_TABLES = ("artesanas", "prendas", "tags_nfc", "registros_lectura",
                 "transacciones", "usuarios", "registros_contables")

# One PL/pgSQL block, so the guard runs inside the database and the migration
# behaves the same online and in offline (--sql) mode.
DROP_SQL = """
DO $legacy$
DECLARE
    t text;
    present text[] := '{}';
    not_empty text[] := '{}';
    has_rows boolean;
BEGIN
    FOREACH t IN ARRAY ARRAY[%s] LOOP
        IF to_regclass(format('%%I.%%I', current_schema(), t)) IS NOT NULL THEN
            present := present || t;
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %%I.%%I)', current_schema(), t) INTO has_rows;
            IF has_rows THEN
                not_empty := not_empty || t;
            END IF;
        END IF;
    END LOOP;
    IF cardinality(not_empty) > 0 THEN
        RAISE EXCEPTION 'legacy tables are not empty, nothing dropped (#134): %%', array_to_string(not_empty, ', ');
    END IF;
    IF cardinality(present) > 0 THEN
        EXECUTE 'DROP TABLE ' || (SELECT string_agg(format('%%I.%%I', current_schema(), x), ', ') FROM unnest(present) AS x);
    END IF;
END
$legacy$
""" % ", ".join(f"'{name}'" for name in LEGACY_TABLES)


def upgrade() -> None:
    op.execute(DROP_SQL)


def downgrade() -> None:
    # Nothing to restore: the tables were empty, were never part of this
    # schema and no code uses them. Their structure stays in the backups taken
    # before this migration (the deploy takes one for any migration).
    pass
