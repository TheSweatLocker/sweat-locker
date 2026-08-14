-- NFL V3 regression model columns (2026-08-13).
--
-- V3 is a situational-regression lens that ADJUSTS the base Matchup-EPA
-- projection using features Matchup-EPA doesn't fully weight:
--   * rest / bye week
--   * wind / temp / roof
--   * division rivalry (closer games historically)
--   * surface (turf/grass minor edge)
--   * short-week (Thu/Mon) fatigue
--
-- These are stored per-game so the compute_primary_play resolver can read
-- v3_spread / v3_total as an independent lens next to Matchup-EPA + Panel + MC.
-- v3 has been part of MLB's lens stack since 2026-05; this extends the same
-- concept to NFL for 5-lens parity.

ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS v3_spread NUMERIC,
  ADD COLUMN IF NOT EXISTS v3_total NUMERIC,
  ADD COLUMN IF NOT EXISTS v3_adjustments JSONB;

-- Partial index for the "has V3" query pattern (used by resolver lens count)
CREATE INDEX IF NOT EXISTS nfl_game_context_v3_present_idx
  ON public.nfl_game_context (game_date DESC)
  WHERE v3_spread IS NOT NULL;

NOTIFY pgrst, 'reload schema';
