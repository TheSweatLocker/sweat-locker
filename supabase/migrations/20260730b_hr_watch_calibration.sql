-- 2026-07-30b · HR Watch calibration columns.
-- Adds `calibrated_hr_prob` (isotonic-fit post-hoc calibration) and
-- `edge_vs_market` (calibrated - book_implied) so the app can rank
-- players by edge instead of raw score, and show an honest probability
-- vs market. See mlb_pipeline/build_hr_watch.py::calibrate_hr_prob and
-- models/hr_watch_calibrator_*.json.

ALTER TABLE mlb_hr_watch
  ADD COLUMN IF NOT EXISTS calibrated_hr_prob NUMERIC,
  ADD COLUMN IF NOT EXISTS edge_vs_market     NUMERIC;

CREATE INDEX IF NOT EXISTS idx_hr_watch_edge ON mlb_hr_watch (game_date DESC, edge_vs_market DESC NULLS LAST);

NOTIFY pgrst, 'reload schema';
