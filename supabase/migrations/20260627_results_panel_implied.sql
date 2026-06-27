-- 2026-06-27 — Archive Panel-implied totals + margins to mlb_game_results.
--
-- The new tier-discipline gate (commits d5e5552 + 6ae1426 + 122ae70) treats
-- the per-pitcher Numbers Panel as a 4th vote alongside v3/v4/jerry. Backtest
-- validation showed Panel beats composite 54-46 on disagreements (n=291 from
-- jerry_cache archive). Now that the gate is live, future backtests need
-- panel_implied snapshotted into the canonical training table per game.
--
-- Computation (mirrors play_of_day.py `_panel_implied` helper):
--   away_bp_ip   = max(0, 9 - away_pitcher_projected_outs / 3)
--   home_bp_ip   = max(0, 9 - home_pitcher_projected_outs / 3)
--   home_scores  = away_pitcher_projected_er + away_bullpen_era * away_bp_ip / 9
--   away_scores  = home_pitcher_projected_er + home_bullpen_era * home_bp_ip / 9
--   panel_implied_total  = home_scores + away_scores
--   panel_implied_margin = home_scores - away_scores  (+ = home wins)
--
-- Inputs already exist on mlb_game_context (projected_er + projected_outs).
-- This migration only adds the OUTPUT columns to mlb_game_results so the
-- audit table doesn't have to recompute them per backtest.

ALTER TABLE mlb_game_results
    ADD COLUMN IF NOT EXISTS panel_implied_total NUMERIC,
    ADD COLUMN IF NOT EXISTS panel_implied_margin NUMERIC,
    ADD COLUMN IF NOT EXISTS away_pitcher_projected_er NUMERIC,
    ADD COLUMN IF NOT EXISTS home_pitcher_projected_er NUMERIC,
    ADD COLUMN IF NOT EXISTS away_pitcher_projected_outs NUMERIC,
    ADD COLUMN IF NOT EXISTS home_pitcher_projected_outs NUMERIC;

COMMENT ON COLUMN mlb_game_results.panel_implied_total IS
  'Numbers Panel-implied total (per-pitcher projected_er + bullpen estimate).
   Snapshotted at game time so backtests can validate the tier gate''s
   "Panel beats composite 54-46 on disagreements" finding against future games
   without recomputing. See play_of_day.py _panel_implied + tier_discipline_gate.';

COMMENT ON COLUMN mlb_game_results.panel_implied_margin IS
  'Panel-implied score margin (home_scores - away_scores, + = home wins).
   Powers the ML gate''s "Panel direction vs resolver direction" check.';

NOTIFY pgrst, 'reload schema';
