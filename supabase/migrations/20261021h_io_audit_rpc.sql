-- == 2026-10-21 - MEASURE THE DISK IO, DO NOT GUESS AT IT ==
--
-- Supabase warned the project was about to deplete its Disk IO Budget. Every
-- statement made about WHY so far has come from an advisor report pasted into
-- a chat window, because nothing in this repo can see the database's own
-- statistics: there is no psql, no Supabase CLI and no connection string in
-- the environment, only PostgREST. So the question "is it fixed yet" has been
-- unanswerable, which is the worst possible state for a budget that throttles
-- the whole app when it empties.
--
-- This is the same move get_rls_audit made for RLS: read the catalog instead
-- of inferring from the outside. Read-only, SECURITY DEFINER because the
-- pg_stat_* views are filtered by the caller's rights, service_role only, and
-- it returns names, counts and sizes -- never row data.
--
-- WHAT IT ANSWERS
--   1. top query shapes by block reads -- the actual IO, not a theory
--   2. indexes that have never been scanned -- pure write amplification
--   3. duplicate indexes -- same table, same column list, counted twice on
--      every insert
--   4. biggest tables and their index overhead
--
-- (2) and (3) matter more than they look. line_history takes roughly 235,000
-- inserts a day and every index on it must be maintained on every one of
-- them. An index nothing reads is a tax paid per write, forever, and the
-- advisor reported TWO identical (sport, commence_time DESC) indexes on that
-- table. Dropping a redundant index is one of the few IO levers that costs
-- nothing in capability -- but it needs the real index names and real scan
-- counts first, which is exactly what has been missing.
--
-- pg_stat_statements may not be installed. The function reports that in its
-- output rather than failing, so a missing extension is a readable answer and
-- not a 500.

CREATE OR REPLACE FUNCTION public.get_io_audit(top_n INT DEFAULT 15)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
  has_pss   BOOLEAN;
  stmts     JSONB := '[]'::JSONB;
  unused    JSONB;
  dupes     JSONB;
  tables    JSONB;
BEGIN
  SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')
    INTO has_pss;

  IF has_pss THEN
    -- shared_blks_read is disk; shared_blks_hit is cache. The budget is spent
    -- on the former, so order by it and report both so a shape that is merely
    -- frequent is not mistaken for one that is expensive.
    BEGIN
      EXECUTE format($q$
        SELECT COALESCE(jsonb_agg(x), '[]'::jsonb) FROM (
          SELECT jsonb_build_object(
                   'calls',      s.calls,
                   'blks_read',  s.shared_blks_read,
                   'blks_hit',   s.shared_blks_hit,
                   'gib_read',   round((s.shared_blks_read * 8192.0)
                                        / 1073741824, 2),
                   'mean_ms',    round(s.mean_exec_time::numeric, 1),
                   'total_ms',   round(s.total_exec_time::numeric, 0),
                   'query',      left(regexp_replace(s.query,
                                      '\s+', ' ', 'g'), 240)
                 ) AS x
            FROM pg_stat_statements s
           WHERE s.query NOT ILIKE '%%pg_stat_statements%%'
           ORDER BY s.shared_blks_read DESC
           LIMIT %s
        ) t$q$, top_n)
      INTO stmts;
    EXCEPTION WHEN OTHERS THEN
      -- Column names on pg_stat_statements moved between PG versions
      -- (total_time -> total_exec_time). Report the reason instead of
      -- returning an empty list that reads as "no IO".
      stmts := jsonb_build_array(jsonb_build_object('error', SQLERRM));
    END;
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
         AND NOT EXISTS (            -- keep constraint-backing indexes
               SELECT 1 FROM pg_constraint c
                WHERE c.conindid = indexrelid)
    ) t;

  -- Same table, same ordered column list, different index. One of each pair
  -- is dead weight on every insert.
  SELECT COALESCE(jsonb_agg(x), '[]'::jsonb) INTO dupes
    FROM (
      SELECT jsonb_build_object(
               'table',   tbl,
               'columns', cols,
               'indexes', names,
               'total',   pg_size_pretty(bytes)
             ) AS x
        FROM (
          SELECT c.relname::TEXT AS tbl,
                 pg_get_indexdef(i.indexrelid)                      AS def,
                 array_agg(ic.relname::TEXT ORDER BY ic.relname)     AS names,
                 i.indkey::TEXT                                     AS cols,
                 SUM(pg_relation_size(i.indexrelid))                AS bytes,
                 COUNT(*)                                           AS n
            FROM pg_index i
            JOIN pg_class c  ON c.oid  = i.indrelid
            JOIN pg_class ic ON ic.oid = i.indexrelid
            JOIN pg_namespace ns ON ns.oid = c.relnamespace
           WHERE ns.nspname = 'public'
           GROUP BY c.relname, i.indkey, pg_get_indexdef(i.indexrelid)
          HAVING COUNT(*) > 1
        ) d
    ) t;

  SELECT COALESCE(jsonb_agg(x), '[]'::jsonb) INTO tables
    FROM (
      SELECT jsonb_build_object(
               'table',       relname,
               'rows',        n_live_tup,
               'inserts',     n_tup_ins,
               'updates',     n_tup_upd,
               'deletes',     n_tup_del,
               'seq_scans',   seq_scan,
               'seq_rows',    seq_tup_read,
               'idx_scans',   idx_scan,
               'heap',        pg_size_pretty(
                                pg_table_size(relid)),
               'indexes',     pg_size_pretty(
                                pg_indexes_size(relid))
             ) AS x
        FROM pg_stat_user_tables
       WHERE schemaname = 'public'
       ORDER BY pg_total_relation_size(relid) DESC
       LIMIT top_n
    ) t;

  RETURN jsonb_build_object(
    'generated_at',          now(),
    'pg_stat_statements',    has_pss,
    'top_shapes_by_disk',    stmts,
    'never_scanned_indexes', unused,
    'duplicate_indexes',     dupes,
    'biggest_tables',        tables
  );
END;
$$;

REVOKE ALL ON FUNCTION public.get_io_audit(INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_io_audit(INT) TO service_role;

COMMENT ON FUNCTION public.get_io_audit(INT) IS
  'Read-only disk-IO posture: top query shapes by blocks READ from disk, '
  'indexes never scanned, duplicate indexes, and per-table heap/index size '
  'with insert and seq-scan counts. service_role only. Exists because '
  'nothing in the repo could see the database''s own statistics, so every '
  'claim about what was burning the IO budget rested on a pasted advisor '
  'report rather than a measurement.';

NOTIFY pgrst, 'reload schema';
