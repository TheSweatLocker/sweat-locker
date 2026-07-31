-- 2026-07-31b · Disable RLS on the new Jerry tables.
-- Same pattern as the 5c518fc hotfix — RLS was blocking pipeline writes
-- because our service key hits these tables without going through auth.
-- These tables have no user-scoped data (they're system-computed content),
-- so RLS adds risk (write breakage) with zero security benefit.

ALTER TABLE jerry_reads                     DISABLE ROW LEVEL SECURITY;
ALTER TABLE external_source_track_record    DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
