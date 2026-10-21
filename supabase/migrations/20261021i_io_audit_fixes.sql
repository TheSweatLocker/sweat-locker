-- == 2026-10-21 - FIX THE IO AUDIT ITSELF (two defects in 20261021h) ==
--
-- 20261021h shipped with two faults, both of which made it report a
-- comfortable answer rather than a true one. Found by running it.
--
-- 1. pg_stat_statements WAS REPORTED INSTALLED AND THEN NOT FOUND.
--    The presence check reads pg_extension, which is schema-agnostic, so it
--    answered true. The query then referenced the relation unqualified under
--    `SET search_path = public, pg_catalog`, and Supabase installs the
--    extension into the `extensions` schema, so the read failed with
--    'relation "pg_stat_statements" does not exist'. The output said
--    installed: true with an empty shape list, which reads as "no IO" --
--    the exact kind of false all-clear this function exists to prevent.
--    Now the extension's real schema is looked up and the relation is
--    fully qualified.
--
-- 2. DUPLICATE DETECTION COULD NEVER FIND A DUPLICATE.
--    It grouped by pg_get_indexdef(), and the index DEFINITION CONTAINS THE
--    INDEX NAME. Two indexes over identical columns necessarily have
--    different names, so their definitions always differ and they never
--    landed in the same group. It reported 0 groups, which I was about to
--    read as "the advisor's two identical (sport, commence_time DESC)
--    indexes are already gone". Grouping is now on what actually defines
--    sameness: the indexed column list, the operator classes, the
--    uniqueness flag and the partial-index predicate.
--
-- WHAT THE WORKING PARTS ALREADY FOUND, and why this matters:
--   line_history   8,484 sequential scans reading 8,710,610,851 rows
--   nfl_player_stats  30,044 seq scans, 1,584,850,428 rows
--   mlb_pipeline_props 31,325 seq scans,  920,792,934 rows
--   nhl_pipeline_props 17,067 seq scans,  723,331,542 rows
-- Roughly 12 billion rows read sequentially. line_history also carries more
-- index (815 MB) than heap (626 MB) across 2.45M inserts, and 226 indexes
-- across the schema have never been scanned once.
--
-- The seq-scan rows are the budget. Identifying WHICH statements cause them
-- needs the shape list, which is why defect 1 has to be fixed before any
-- more guessing.

CREATE OR REPLACE FUNCTION public.get_io_audit(top_n INT DEFAULT 15)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
  pss_schema TEXT;
  stmts      JSONB := '[]'::JSONB;
  unused     JSONB;
  dupes      JSONB;
  tables     JSONB;
BEGIN
  -- The extension's OWN schema, not an assumed one. Supabase puts it in
  -- `extensions`; a bare reference under search_path = public, pg_catalog
  -- therefore fails even though pg_extension says it is installed.
  SELECT ns.nspname INTO pss_schema
    FROM pg_extension e
    JOIN pg_namespace ns ON ns.oid = e.extnamespace
   WHERE e.extname = 'pg_stat_statements';

  IF pss_schema IS NOT NULL THEN
    BEGIN
      EXECUTE format($q$
        SELECT COALESCE(jsonb_agg(x), '[]'::jsonb) FROM (
          SELECT jsonb_build_object(
                   'calls',     s.calls,
                   'gib_read',  round((s.shared_blks_read * 8192.0)
                                       / 1073741824, 2),
                   'blks_read', s.shared_blks_read,
                   'blks_hit',  s.shared_blks_hit,
                   'mean_ms',   round(s.mean_exec_time::numeric, 1),
                   'total_s',   round((s.total_exec_time / 1000)::numeric, 0),
                   'rows',      s.rows,
                   'query',     left(regexp_replace(s.query, '\s+', ' ', 'g'),
                                     260)
                 ) AS x
            FROM %I.pg_stat_statements s
           WHERE s.query NOT ILIKE '%%pg_stat_statements%%'
             AND s.query NOT ILIKE '%%get_io_audit%%'
           ORDER BY s.shared_blks_read DESC
           LIMIT %s
        ) t$q$, pss_schema, top_n)
      INTO stmts;
    EXCEPTION WHEN OTHERS THEN
      stmts := jsonb_build_array(jsonb_build_object(
                 'error', SQLERRM, 'schema', pss_schema));
    END;
  ELSE
    stmts := jsonb_build_array(jsonb_build_object(
               'error', 'pg_stat_statements not installed'));
  END IF;

  SELECT COALESCE(jsonb_agg(x ORDER BY (x->>'size_bytes')::BIGINT DESC),
                  '[]'::jsonb) INTO unused
    FROM (
      SELECT jsonb_build_object(
               'table',      relname,
               'index',      indexrelname,
               'scans',      idx_scan,
               'size',       pg_size_pretty(pg_relation_size(indexrelid)),
               'size_bytes', pg_relation_size(indexrelid)
             ) AS x
        FROM pg_stat_user_indexes
       WHERE schemaname = 'public'
         AND idx_scan = 0
         AND NOT EXISTS (SELECT 1 FROM pg_constraint c
                          WHERE c.conindid = indexrelid)
    ) t;

  -- Sameness is the column list + operator classes + uniqueness + predicate.
  -- NOT the index definition, which embeds the name and so can never match.
  SELECT COALESCE(jsonb_agg(x ORDER BY (x->>'bytes')::BIGINT DESC),
                  '[]'::jsonb) INTO dupes
    FROM (
      SELECT jsonb_build_object(
               'table',   tbl,
               'n',       n,
               'indexes', names,
               'defs',    defs,
               'total',   pg_size_pretty(bytes),
               'bytes',   bytes
             ) AS x
        FROM (
          SELECT c.relname::TEXT AS tbl,
                 array_agg(ic.relname::TEXT ORDER BY ic.relname) AS names,
                 array_agg(pg_get_indexdef(i.indexrelid)
                           ORDER BY ic.relname)                  AS defs,
                 SUM(pg_relation_size(i.indexrelid))             AS bytes,
                 COUNT(*)                                        AS n
            FROM pg_index i
            JOIN pg_class c  ON c.oid  = i.indrelid
            JOIN pg_class ic ON ic.oid = i.indexrelid
            JOIN pg_namespace ns ON ns.oid = c.relnamespace
           WHERE ns.nspname = 'public'
           GROUP BY c.relname,
                    i.indkey::TEXT,
                    i.indclass::TEXT,
                    i.indisunique,
                    COALESCE(pg_get_expr(i.indpred, i.indrelid), '')
          HAVING COUNT(*) > 1
        ) d
    ) t;

  SELECT COALESCE(jsonb_agg(x), '[]'::jsonb) INTO tables
    FROM (
      SELECT jsonb_build_object(
               'table',     relname,
               'rows',      n_live_tup,
               'inserts',   n_tup_ins,
               'updates',   n_tup_upd,
               'deletes',   n_tup_del,
               'seq_scans', seq_scan,
               'seq_rows',  seq_tup_read,
               'idx_scans', COALESCE(idx_scan, 0),
               'idx_rows',  COALESCE(idx_tup_fetch, 0),
               'heap',      pg_size_pretty(pg_table_size(relid)),
               'indexes',   pg_size_pretty(pg_indexes_size(relid))
             ) AS x
        FROM pg_stat_user_tables
       WHERE schemaname = 'public'
       ORDER BY seq_tup_read DESC NULLS LAST
       LIMIT top_n
    ) t;

  -- ══ HOW OLD ARE THESE STATISTICS? ══
  -- Every count above is cumulative since the last stats reset, so
  -- idx_scan = 0 means "never scanned SINCE THE RESET" and nothing more. If
  -- the counters were cleared an hour ago, a perfectly load-bearing index
  -- that only serves a nightly job reads as dead, and dropping it on that
  -- evidence would be a self-inflicted outage. Reported alongside the
  -- numbers so a reader cannot act on the counts without seeing the window
  -- they cover.
  RETURN jsonb_build_object(
    'generated_at',          now(),
    'stats_reset',           (SELECT stats_reset FROM pg_stat_database
                               WHERE datname = current_database()),
    'stats_age_hours',       (SELECT round(EXTRACT(EPOCH FROM
                                 (now() - stats_reset)) / 3600.0, 1)
                                FROM pg_stat_database
                               WHERE datname = current_database()),
    'pss_schema',            pss_schema,
    'top_shapes_by_disk',    stmts,
    'never_scanned_indexes', unused,
    'duplicate_indexes',     dupes,
    'worst_seq_scan_tables', tables
  );
END;
$$;

REVOKE ALL ON FUNCTION public.get_io_audit(INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_io_audit(INT) TO service_role;

NOTIFY pgrst, 'reload schema';
