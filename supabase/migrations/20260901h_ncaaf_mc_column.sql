-- ncaaf_game_context.mc_probabilities column (2026-09-01)
--
-- 5th lens for NCAAF's model stack. Mirrors:
--   20260813_nfl_mc_column.sql   — NFL equivalent
--   20260814_ncaab_mc_column.sql — NCAAB equivalent
--
-- Populated by mlb_pipeline/ncaaf_mc_simulator.py after ncaaf_game_context
-- runs (needs projected_spread + projected_total on the ctx row first).
--
-- Payload shape (same as NFL/MLB/NCAAB so LensGrid + NumbersPanel render
-- uniformly cross-sport):
--   mc_p_home, mc_p_away              — win probabilities
--   mc_expected_margin, _total        — mean of 10k sim scores
--   mc_stddev_margin                  — sim dispersion (calibration signal)
--   mc_p_over_line                    — over% vs close_total
--   mc_confidence_high                — |margin|>7 AND stddev<19.5 (bool)
--   generated_at                      — ISO ts

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS mc_probabilities JSONB;

-- Partial index on games with MC populated — matches NFL pattern; used by
-- future audits + calibration cross-checks.
CREATE INDEX IF NOT EXISTS idx_ncaaf_game_context_has_mc
  ON public.ncaaf_game_context (game_date)
  WHERE mc_probabilities IS NOT NULL;

NOTIFY pgrst, 'reload schema';
