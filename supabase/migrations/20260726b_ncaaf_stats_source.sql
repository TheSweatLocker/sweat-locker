-- ============================================================
-- NCAAF stats_source tracking — mirrors 20260725b_nfl_stats_source.
-- ============================================================
-- CFB has the same Week-1 problem as NFL: current-season stats don't
-- populate until games are played. Without a fallback, Aug 22 Week 1
-- games emit zero picks. Portal turnover + coaching changes mean prior-
-- season SP+/EPA carries forward with real error bars — hence
-- regression-to-mean with a stronger shrink than NFL (0.5 vs 0.4).
-- ============================================================
-- Values:
--   'current'                 — current-year, ≥3 games/team avg
--   'prior_season_regressed'  — prior season blended 0.5/0.5 to mean
--   'none'                    — neither season populated
-- ============================================================

ALTER TABLE ncaaf_game_context
  ADD COLUMN IF NOT EXISTS stats_source TEXT;

COMMENT ON COLUMN ncaaf_game_context.stats_source IS
  'Which season fed SP+/EPA: current | prior_season_regressed | none. Aug-Sep games run on prior_season_regressed until Week 4.';

NOTIFY pgrst, 'reload schema';
