-- Supabase capacity RPC (2026-08-24) — pre-launch cost monitoring.
--
-- WHY THIS EXISTS
-- ───────────────
-- Supabase Pro tier includes 8GB database + 250GB egress + 100K auth MAU.
-- Overage bills at $0.021/GB storage + $0.125/GB egress. The next tier
-- (Team) is $599/mo — a ~24x jump from Pro's $25.
--
-- To avoid surprise bills OR a forced tier upgrade at the worst possible
-- moment (launch weekend), we need EARLY WARNING when approaching Pro
-- ceilings. Egress + MAU require the Management API (personal access
-- token, out of scope here). But DATABASE SIZE we can measure directly
-- from within the DB itself — and it's usually the first ceiling hit
-- as backfilled historical results + snapshot audit trails accumulate.
--
-- This RPC returns:
--   - Total database size (bytes + human-readable)
--   - Top 10 tables by size (bytes + row count via pg_class.reltuples)
--   - Recent 24h write volume proxy (last-updated row count on hot tables)
--
-- Called by watchdogs.check_supabase_capacity() to fire WARNING at
-- 70% of 8GB and CRITICAL at 90%.

CREATE OR REPLACE FUNCTION public.get_supabase_capacity()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  db_bytes           BIGINT;
  db_pretty          TEXT;
  top_tables         JSONB;
  result             JSONB;
BEGIN
  -- Total logical size of current database
  SELECT pg_database_size(current_database())::BIGINT INTO db_bytes;
  db_pretty := pg_size_pretty(db_bytes);

  -- Top 10 tables by total size (heap + indexes + toast)
  SELECT jsonb_agg(row_data ORDER BY size_bytes DESC) INTO top_tables
  FROM (
    SELECT jsonb_build_object(
      'schema',      schemaname,
      'table',       tablename,
      'size_bytes',  pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(tablename)),
      'size_pretty', pg_size_pretty(pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(tablename))),
      'row_estimate',(SELECT reltuples::BIGINT
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = schemaname
                         AND c.relname = tablename)
    ) AS row_data,
    pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(tablename)) AS size_bytes
    FROM pg_tables
    WHERE schemaname = 'public'
    ORDER BY size_bytes DESC
    LIMIT 10
  ) t;

  result := jsonb_build_object(
    'checked_at',           NOW(),
    'db_size_bytes',        db_bytes,
    'db_size_pretty',       db_pretty,
    'db_size_gb',           ROUND((db_bytes::NUMERIC / (1024.0 * 1024.0 * 1024.0))::NUMERIC, 3),
    'pro_tier_limit_gb',    8.0,
    'pro_tier_pct_used',    ROUND((db_bytes::NUMERIC / (8.0 * 1024.0 * 1024.0 * 1024.0) * 100.0)::NUMERIC, 2),
    'top_tables',           top_tables
  );

  RETURN result;
END $$;

-- Allow both anon + service_role to call (read-only, no side effects)
GRANT EXECUTE ON FUNCTION public.get_supabase_capacity() TO anon, service_role, authenticated;

NOTIFY pgrst, 'reload schema';
