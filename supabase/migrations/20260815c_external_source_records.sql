-- 2026-08-15 pm — superseded on discovery.
--
-- This migration originally created public.external_source_records. On
-- populating it, we discovered public.external_source_track_record
-- already exists (from 20260731_jerry_synthesis_tables.sql) with an
-- equivalent schema (source, sport, surface, window_days, n_wins,
-- n_losses, n_pushes, hit_rate, roi). Two parallel tables would drift.
--
-- Rewritten to drop the redundant table. compute_external_source_records.py
-- now writes to external_source_track_record instead. Re-apply to remove
-- the empty duplicate cleanly.

DROP TABLE IF EXISTS public.external_source_records;

NOTIFY pgrst, 'reload schema';
