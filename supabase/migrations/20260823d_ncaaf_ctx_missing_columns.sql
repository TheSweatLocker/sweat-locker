-- ncaaf_game_context — add 5 columns referenced by writer + signal_sources
-- but never migrated. Every upsert since 2026-08-22 was 400ing on the first
-- unknown column, blocking ALL games from writing. Discovered 2026-08-23
-- while diagnosing why Next Week tab was empty even though results table
-- had 41 Week 1 games.
--
-- Columns:
--   home_sp_plus / away_sp_plus       — aliased from sp_overall; signal_sources
--                                        rows (ncaaf_sp_plus_edge_home/_away)
--                                        expect these names
--   sp_plus_matchup_total             — sum for over/under signals
--   home_returning_production         — blended off+def average; signals expect
--   away_returning_production         — this rollup instead of computing in-signal

ALTER TABLE IF EXISTS public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_sp_plus              NUMERIC,
  ADD COLUMN IF NOT EXISTS away_sp_plus              NUMERIC,
  ADD COLUMN IF NOT EXISTS sp_plus_matchup_total     NUMERIC,
  ADD COLUMN IF NOT EXISTS home_returning_production NUMERIC,
  ADD COLUMN IF NOT EXISTS away_returning_production NUMERIC;

NOTIFY pgrst, 'reload schema';
