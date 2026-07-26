-- ============================================================
-- Track which season's team stats fed each nfl_game_context row.
-- Enables downstream (Jerry rules, app UI, POTD tier gate) to
-- honestly disclose when we're operating on prior-season data
-- (Weeks 1-3 of 2026 season). See nfl_game_context.py fallback logic.
-- ============================================================
-- Values:
--   'current'                 — current-year stats, ≥4 games/team avg
--   'prior_season_regressed'  — prior season blended 0.6/0.4 toward mean
--   'none'                    — no stats available on either season
-- ============================================================

ALTER TABLE nfl_game_context
  ADD COLUMN IF NOT EXISTS stats_source TEXT;

COMMENT ON COLUMN nfl_game_context.stats_source IS
  'Which season fed the EPA numbers: current | prior_season_regressed | none. Early-season Week 1-3 games run on prior_season_regressed.';

NOTIFY pgrst, 'reload schema';
