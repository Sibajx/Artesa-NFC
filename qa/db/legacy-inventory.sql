-- Read-only inventory of the tables in the production database (issue #134,
-- Brain B-023). Schema, statistics and relationships ONLY: no row contents
-- are selected. The whole script runs in a READ ONLY transaction, so
-- PostgreSQL itself refuses any write even if this file were edited.
--
-- Run on easerver (prints to the terminal only; nothing is written):
--   cd /home/energias/artesa-nfc
--   ( set -a; . shared/.env; set +a; psql "$DATABASE_URL" -X -q -f qa-legacy-inventory.sql )
-- (copy this file there first). Paste the output back; it contains no data.

\pset pager off
\pset footer off
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '30s';

\echo '== 1. Tables in public: owner, approx/exact rows, size, last write activity'
SELECT c.relname                                   AS table_name,
       pg_get_userbyid(c.relowner)                 AS owner,
       c.reltuples::bigint                         AS approx_rows,
       s.n_live_tup                                AS live_rows,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
       s.n_tup_ins AS inserts, s.n_tup_upd AS updates, s.n_tup_del AS deletes,
       greatest(s.last_autoanalyze, s.last_analyze, s.last_autovacuum, s.last_vacuum) AS last_maintenance
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname;

\echo '== 2. Exact row counts (count(*) only)'
SELECT format('SELECT %L AS table_name, count(*) AS rows FROM public.%I', c.relname, c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname
\gexec

\echo '== 3. Columns of the legacy tables (names and types only)'
SELECT table_name, ordinal_position AS pos, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name NOT IN ('artisan', 'piece', 'media_asset', 'certificate', 'nfc_tag', 'audit_event', 'alembic_version')
ORDER BY table_name, ordinal_position;

\echo '== 4. Columns whose NAME suggests sensitive data (heuristic, names only)'
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name NOT IN ('artisan', 'piece', 'media_asset', 'certificate', 'nfc_tag', 'audit_event', 'alembic_version')
  AND column_name ~* '(pass|hash|token|secret|key|email|correo|tel|phone|rfc|curp|direcc|address|monto|amount|saldo|tarjeta|card|cuenta|account|nombre|name|ip)'
ORDER BY table_name, column_name;

\echo '== 5. Foreign keys touching any public table'
SELECT conrelid::regclass  AS from_table,
       confrelid::regclass AS to_table,
       conname             AS constraint_name
FROM pg_constraint
WHERE contype = 'f' AND connamespace = 'public'::regnamespace
ORDER BY 1, 2;

\echo '== 6. Newest timestamp per date/time column of the legacy tables (one value each, no rows)'
SELECT format('SELECT %L AS table_name, %L AS column_name, max(%I)::text AS newest FROM public.%I',
              table_name, column_name, column_name, table_name)
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name NOT IN ('artisan', 'piece', 'media_asset', 'certificate', 'nfc_tag', 'audit_event', 'alembic_version')
  AND data_type IN ('timestamp without time zone', 'timestamp with time zone', 'date')
ORDER BY table_name, column_name
\gexec

\echo '== 7. Other schemas and roles owning objects (context)'
SELECT nspname AS schema, pg_get_userbyid(nspowner) AS owner FROM pg_namespace
WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' ORDER BY 1;

ROLLBACK;
