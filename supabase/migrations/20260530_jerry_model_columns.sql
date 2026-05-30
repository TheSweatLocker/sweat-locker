-- 2026-05-30 — Jerry Model run-projection columns.
--
-- The Jerry Model is a transparent linear-formula projection per team
-- using inning-bucket simulation, mastery, recency-weighted offense,
-- home/away splits, bullpen gas penalties, and park/weather adjustments.
-- See mlb_pipeline/jerry_model.py for the math.
--
-- Runs in shadow mode alongside v3/v4 for 2-4 weeks before any user-
-- facing surfacing. Predictions land in both mlb_game_context (live
-- slate) and mlb_game_results (training/audit). Components stored as
-- JSONB so per-game breakdowns are queryable for backtest tuning.

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS jerry_pred_away_runs NUMERIC,
    ADD COLUMN IF NOT EXISTS jerry_pred_home_runs NUMERIC,
    ADD COLUMN IF NOT EXISTS jerry_pred_total NUMERIC,
    ADD COLUMN IF NOT EXISTS jerry_pred_spread NUMERIC,
    ADD COLUMN IF NOT EXISTS jerry_components JSONB,
    ADD COLUMN IF NOT EXISTS jerry_weights_version TEXT;

ALTER TABLE mlb_game_results
    ADD COLUMN IF NOT EXISTS jerry_pred_away_runs NUMERIC,
    ADD COLUMN IF NOT EXISTS jerry_pred_home_runs NUMERIC,
    ADD COLUMN IF NOT EXISTS jerry_pred_total NUMERIC,
    ADD COLUMN IF NOT EXISTS jerry_pred_spread NUMERIC,
    ADD COLUMN IF NOT EXISTS jerry_components JSONB,
    ADD COLUMN IF NOT EXISTS jerry_weights_version TEXT;

COMMENT ON COLUMN mlb_game_context.jerry_pred_total IS
  'Jerry Model total run projection. Shadow mode 5/30-2026-06-15. NOT user-visible
   while audit accumulates. See jerry_model.py for the math, _backtest_jerry.py
   for hit rate vs v3/v4.';

COMMENT ON COLUMN mlb_game_context.jerry_pred_spread IS
  'Jerry Model spread = jerry_pred_home_runs - jerry_pred_away_runs. Positive =
   home favored. Same shadow caveat as jerry_pred_total.';

COMMENT ON COLUMN mlb_game_context.jerry_components IS
  'Per-team breakdown {away: {...}, home: {...}} of Jerry projection components:
   wrc_blend, offense_mult, mastery_mult, runs_1_3/4_6/7_9, park/temp/wind mults.
   Used by backtest scripts to identify which factors drive errors.';

NOTIFY pgrst, 'reload schema';
