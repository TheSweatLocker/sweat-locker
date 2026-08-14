-- NFL V4 XGBoost model columns (2026-08-13).
--
-- V4 is the 5th and final lens in NFL's model stack (after Panel, Matchup-EPA,
-- MC, V3, and Jerry). Data-driven XGBoost model trained on 2020-2024 nflverse
-- regular season + playoffs, predicting spread + total from feature vector.
--
-- Where V3 uses hand-coded coefficients on 8 situational factors, V4 lets the
-- data pick the weighting. Model file lives at models/nfl_v4_spread.pkl and
-- models/nfl_v4_total.pkl; inference reads current-week features and writes
-- v4_spread + v4_total + v4_confidence back to nfl_game_context.
--
-- v4_confidence is the model's predicted stddev of the target — lower = more
-- certain. Used by compute_primary_play to gate PRIME tier assignments.

ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS v4_spread NUMERIC,
  ADD COLUMN IF NOT EXISTS v4_total NUMERIC,
  ADD COLUMN IF NOT EXISTS v4_confidence NUMERIC,
  ADD COLUMN IF NOT EXISTS v4_features_used JSONB;

-- Partial index for lens-count queries
CREATE INDEX IF NOT EXISTS nfl_game_context_v4_present_idx
  ON public.nfl_game_context (game_date DESC)
  WHERE v4_spread IS NOT NULL;

NOTIFY pgrst, 'reload schema';
