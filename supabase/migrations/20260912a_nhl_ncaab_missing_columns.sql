-- 2026-09-12: NHL/NCAAB schema gap fix
--
-- Andy audit surfaced repeated 42703 errors in postgres logs:
--   column nhl_game_context.close_spread does not exist
--   column ncaab_game_context.close_spread does not exist
--   column ncaab_game_context.close_home_ml does not exist
--   column ncaab_game_results.run_line_result does not exist
--
-- Root cause: `app/utils/betResolver.ts:101` hardcodes run_line_result
-- in the SELECT for ALL sports. Non-MLB sports 400 on every bet resolve
-- attempt, flooding the log. Similarly Python + client code queries
-- close_spread/close_total/close_home_ml on all *_game_context tables
-- assuming MLB parity — NCAAB + NHL never got those columns because
-- neither had historical odds ingested (see project_ncaab_data_gap_817
-- + project_nhl_rebuild_status_817).
--
-- Fix: add the missing columns as NULLable across all affected tables.
-- Non-MLB sports get NULL values (no odds ingested yet); MLB unaffected.
-- Callers that check for null before use will work; callers that assumed
-- MLB parity get NULL back instead of a 42703 error.
--
-- Downstream backfill: once NHL + NCAAB odds ingest lands (NHL Oct 8,
-- NCAAB Nov 3), these columns get populated from close_lines_freeze.
--
-- Additionally: run_line_result on non-MLB results tables allows the
-- client betResolver to query the same select uniformly. Value stays
-- NULL for non-MLB sports (they use spread_result instead) — grader
-- alias logic in aggregate_daily_records already handles that path.

-- ─── NHL game context: MLB-parity close_* columns ─────────────────────
ALTER TABLE public.nhl_game_context
  ADD COLUMN IF NOT EXISTS close_spread NUMERIC,
  ADD COLUMN IF NOT EXISTS close_total NUMERIC,
  ADD COLUMN IF NOT EXISTS close_home_ml INTEGER,
  ADD COLUMN IF NOT EXISTS close_away_ml INTEGER,
  ADD COLUMN IF NOT EXISTS close_puckline_home NUMERIC;

-- ─── NCAAB game context: MLB-parity close_* columns ────────────────────
ALTER TABLE public.ncaab_game_context
  ADD COLUMN IF NOT EXISTS close_spread NUMERIC,
  ADD COLUMN IF NOT EXISTS close_total NUMERIC,
  ADD COLUMN IF NOT EXISTS close_home_ml INTEGER,
  ADD COLUMN IF NOT EXISTS close_away_ml INTEGER;

-- ─── Results tables: uniform run_line_result column ────────────────────
-- Non-MLB uses spread_result operationally; run_line_result stays NULL
-- for those sports. Column exists just to satisfy the uniform SELECT
-- that betResolver.ts uses across every sport.
ALTER TABLE public.nfl_game_results
  ADD COLUMN IF NOT EXISTS run_line_result TEXT;
ALTER TABLE public.ncaaf_game_results
  ADD COLUMN IF NOT EXISTS run_line_result TEXT;
ALTER TABLE public.ncaab_game_results
  ADD COLUMN IF NOT EXISTS run_line_result TEXT,
  ADD COLUMN IF NOT EXISTS close_home_ml INTEGER,
  ADD COLUMN IF NOT EXISTS close_away_ml INTEGER;
ALTER TABLE public.nba_game_results
  ADD COLUMN IF NOT EXISTS run_line_result TEXT;
ALTER TABLE public.nhl_game_results
  ADD COLUMN IF NOT EXISTS run_line_result TEXT;

-- Reload PostgREST schema cache so the new columns are queryable
-- immediately (feedback_migration_pgrst_reload standard).
NOTIFY pgrst, 'reload schema';
