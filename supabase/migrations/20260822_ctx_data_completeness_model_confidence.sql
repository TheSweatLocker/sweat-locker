-- Add the two Phase 1 (2026-06-18) mlb_game_context columns that were
-- referenced in the write payload but never migrated to schema. Result:
-- every game_context write returned 400 on the first attempt, the
-- strip-retry logic dropped these two columns, then re-POSTed — two
-- wasted round-trips per game. On a 15-game slate that's 30 wasted
-- HTTP calls per pipeline cycle. Root-cause fix: add the columns so
-- the retry loop never fires.
--
-- data_completeness: 'HIGH' / 'MEDIUM' / 'LOW' — alias for existing
--   'confidence' column but with non-misleading naming for callers.
--   Signals park + weather data availability, NOT model conviction.
--
-- model_confidence: 'STRONG' / 'EDGE' / 'NEUTRAL' / 'CONFLICTED' —
--   computed from projection-vs-line gap + model agreement further
--   down in game_context.py. THIS is the real model-conviction proxy.

ALTER TABLE public.mlb_game_context
  ADD COLUMN IF NOT EXISTS data_completeness TEXT,
  ADD COLUMN IF NOT EXISTS model_confidence TEXT;

COMMENT ON COLUMN public.mlb_game_context.data_completeness IS
  'Park+weather data-availability flag: HIGH (both) / MEDIUM (park only) / LOW.';
COMMENT ON COLUMN public.mlb_game_context.model_confidence IS
  'Model-conviction proxy from projection vs close + model agreement: STRONG / EDGE / NEUTRAL / CONFLICTED.';

NOTIFY pgrst, 'reload schema';
