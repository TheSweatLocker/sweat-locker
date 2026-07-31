-- 2026-07-31e · Hotfix — Supabase auto-enabled RLS on prop_jerry_reads
-- despite the DISABLE clause in 20260731d. Same pattern as we saw with
-- jerry_reads / external_source_track_record on 20260731b.
-- System-computed table, no user scope, safe to disable.

ALTER TABLE prop_jerry_reads DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
