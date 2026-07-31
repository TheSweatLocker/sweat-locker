-- 2026-07-31h · Hotfix — RLS was blocking anon reads on feature_flags.
-- Same class of bug as jerry_reads / prop_jerry_reads / external_source_track_record.
-- Supabase auto-enables RLS on new tables regardless of the DISABLE clause
-- in the CREATE TABLE migration.
--
-- User symptom: only MLB showed in sport picker after 20260731g applied
-- (which flipped every sport_tab to true in the DB). Anon-key reads
-- returned nothing, featureFlags map stayed empty, non-MLB sports failed
-- the isFeatureOn gate.
--
-- feature_flags is read-only config (system-computed, no user scope),
-- safe to disable RLS.

ALTER TABLE feature_flags DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
