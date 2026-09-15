-- 2026-09-15 v1.06: rolling L4 team form on ncaaf_game_context.
-- Feeds ncaaf_total_logreg. Trainer already computes these features from
-- ncaaf_game_results; adding them to ctx closes the gap so inference-time
-- reads via _lr_predict_total actually see real values instead of imputer
-- medians. Expected lift: another +1-2pp on top of v1.05's +2.5pp.

ALTER TABLE ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_l4_ppg        NUMERIC,
  ADD COLUMN IF NOT EXISTS home_l4_pa         NUMERIC,
  ADD COLUMN IF NOT EXISTS home_l4_total_avg  NUMERIC,
  ADD COLUMN IF NOT EXISTS home_l4_over_rate  NUMERIC,
  ADD COLUMN IF NOT EXISTS away_l4_ppg        NUMERIC,
  ADD COLUMN IF NOT EXISTS away_l4_pa         NUMERIC,
  ADD COLUMN IF NOT EXISTS away_l4_total_avg  NUMERIC,
  ADD COLUMN IF NOT EXISTS away_l4_over_rate  NUMERIC;

NOTIFY pgrst, 'reload schema';
