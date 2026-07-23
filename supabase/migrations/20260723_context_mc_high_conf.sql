-- 2026-07-23 — MC high-confidence flag on mlb_game_context.
--
-- Post MC v2 backtest (project_mc_v2_backtest_723) showed rich MC at
-- 80%+ confidence hits 71.1% on n=135 games (verified after mastery
-- mult unlock). That's real actionable signal — card should surface it
-- like PRIME/STRONG.
--
-- Detector: written by enrich_monte_carlo when abs(p_home_win - 0.5) >= 0.30.
-- App reads mc_high_conf_flag to render the "MC 82% HOME" chip.

ALTER TABLE mlb_game_context
  ADD COLUMN IF NOT EXISTS mc_high_conf_flag  BOOLEAN,
  ADD COLUMN IF NOT EXISTS mc_high_conf_side  TEXT,        -- 'HOME' | 'AWAY'
  ADD COLUMN IF NOT EXISTS mc_high_conf_pct   NUMERIC;     -- 0.0-1.0

CREATE INDEX IF NOT EXISTS idx_mlb_game_context_mc_high_conf
  ON mlb_game_context (mc_high_conf_flag)
  WHERE mc_high_conf_flag = TRUE;

NOTIFY pgrst, 'reload schema';
