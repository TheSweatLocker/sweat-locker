-- 2026-07-22 — Add panel_implied_total + panel_implied_margin to mlb_game_context.
--
-- Yesterday's audit (7/21) showed Panel = 72% on sides (8-3 of 11 graded
-- games) — the best single lens layer of the night. But Panel currently
-- only lives in mlb_game_results (post-resolve). Adding it to game_context
-- lets compute_primary_play + weighted_composite_spread use the
-- with-panel variant (60d audit: 62.0% vs panel-free 60.4%) at pick time.
--
-- Formula (mirrors play_of_day._panel_implied + backfill_panel_implied):
--   away_bp_ip   = max(0, 9 - away_pitcher_projected_outs / 3)
--   home_bp_ip   = max(0, 9 - home_pitcher_projected_outs / 3)
--   home_scores  = away_pitcher_projected_er + away_bullpen_era * away_bp_ip / 9
--   away_scores  = home_pitcher_projected_er + home_bullpen_era * home_bp_ip / 9
--   panel_implied_total  = home_scores + away_scores
--   panel_implied_margin = home_scores - away_scores  (+ = home wins)
--
-- Inputs already exist on mlb_game_context (away/home_pitcher_projected_er
-- + _outs, away/home_bullpen_era). game_context.py will populate on write.

ALTER TABLE mlb_game_context
  ADD COLUMN IF NOT EXISTS panel_implied_total  NUMERIC,
  ADD COLUMN IF NOT EXISTS panel_implied_margin NUMERIC;

CREATE INDEX IF NOT EXISTS idx_mlb_game_context_panel_margin
  ON mlb_game_context (panel_implied_margin)
  WHERE panel_implied_margin IS NOT NULL;

NOTIFY pgrst, 'reload schema';
