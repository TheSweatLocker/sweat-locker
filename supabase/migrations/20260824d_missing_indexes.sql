-- Missing indexes on ordered-query columns (2026-08-24).
--
-- WHY THIS EXISTS
-- ───────────────
-- Today's compute crisis on Nano tier was largely driven by full table
-- scans on ordered columns without indexes. When PostgREST orders by a
-- non-indexed column with LIMIT, Postgres has to scan the entire table
-- to find the top N rows. On a busy Sunday with concurrent workflows,
-- this hammered CPU/memory to 94% and cascaded timeouts everywhere.
--
-- Specific incidents:
--   ladder_state order by updated_at → full scan (only ~50 rows so not
--     catastrophic, but multiplied by every ladder read)
--   line_history order by captured_at → 37K row scan for "last write" checks
--   odds_cache order by fetched_at → JSONB-heavy table scan
--   line_movement_flags freshness queries → hit table without index
--
-- All below use IF NOT EXISTS so re-running is safe. Partial index on
-- game_date DESC to keep index small (most queries only look at recent
-- data anyway).

-- ladder_state — last_updated_at ordering used by app + fix scripts
-- (column is `last_updated_at` not `updated_at` — confirmed 8/24)
CREATE INDEX IF NOT EXISTS idx_ladder_state_last_updated_at
  ON public.ladder_state (last_updated_at DESC);

-- ladder_rung — game_date filtering (already may exist, using IF NOT EXISTS)
CREATE INDEX IF NOT EXISTS idx_ladder_rung_game_date
  ON public.ladder_rung (game_date DESC);

-- line_history — captured_at ordering for "last write" freshness checks
CREATE INDEX IF NOT EXISTS idx_line_history_captured_at
  ON public.line_history (captured_at DESC);

-- line_history — (sport, captured_at) for per-sport freshness queries
CREATE INDEX IF NOT EXISTS idx_line_history_sport_captured
  ON public.line_history (sport, captured_at DESC);

-- odds_cache — fetched_at ordering for write_line_history + freshness
CREATE INDEX IF NOT EXISTS idx_odds_cache_fetched_at
  ON public.odds_cache (fetched_at DESC);

-- odds_cache — (cache_key LIKE prefix, fetched_at) for MLB-only queries
CREATE INDEX IF NOT EXISTS idx_odds_cache_key_fetched
  ON public.odds_cache (cache_key, fetched_at DESC);

-- line_movement_flags — freshness lookup by sport
CREATE INDEX IF NOT EXISTS idx_line_movement_flags_sport_detected
  ON public.line_movement_flags (sport, first_seen_at DESC);

-- jerry_cache — cache_key ordering (used by POTD + narrative lookups)
CREATE INDEX IF NOT EXISTS idx_jerry_cache_fetched_at
  ON public.jerry_cache (fetched_at DESC);

-- mlb_pipeline_props — game_date + tier is the hot query path from Sharp Card
-- Partial index on PRIME/STRONG only keeps it tiny — LEAN rows don't need it
CREATE INDEX IF NOT EXISTS idx_mlb_props_hot_tier
  ON public.mlb_pipeline_props (game_date DESC, tier)
  WHERE tier IN ('PRIME', 'STRONG');

-- mlb_game_context — game_date is the primary filter (should already exist,
-- confirming)
CREATE INDEX IF NOT EXISTS idx_mlb_game_context_game_date
  ON public.mlb_game_context (game_date DESC);

-- prop_playbook_decisions — the ungraded partial index already exists per
-- 20260820_prop_playbook_grading.sql; adding sport+date rollup for
-- backtest queries
CREATE INDEX IF NOT EXISTS idx_ppd_sport_game_date
  ON public.prop_playbook_decisions (sport, game_date DESC);

NOTIFY pgrst, 'reload schema';
