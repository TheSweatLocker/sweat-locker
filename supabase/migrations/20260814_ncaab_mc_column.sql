-- NCAAB MC probabilities column (2026-08-14).
--
-- Session 2 of NCAAB 5-lens build. Adds mc_probabilities blob to
-- ncaab_game_context so the possession-based Monte Carlo simulator
-- can write win prob + expected margin + expected total per game.
--
-- Same shape as mlb_game_context.mc_probabilities + nfl_game_context.
-- mc_probabilities so downstream lens-count logic (anti-consensus rule,
-- MC HIGH-CONF chip) reads a single field name across sports.
--
-- Shape (JSONB):
--   {
--     "mc_p_home": 0.617,           // 10k-sim home win probability
--     "mc_p_away": 0.383,
--     "mc_expected_margin": 4.8,    // home - away expected pts
--     "mc_expected_total": 145.2,
--     "mc_stddev_margin": 13.4,
--     "mc_p_over_line": 0.53,       // if close_total present
--     "mc_confidence_high": false,  // margin |6|+ AND stddev <14
--     "generated_at": "2026-08-14T..."
--   }
--
-- NCAAB has more variance per game than NFL (fewer possessions per team
-- than pro football drives). Confidence threshold calibrated later after
-- Session 4 backtest.

ALTER TABLE public.ncaab_game_context
  ADD COLUMN IF NOT EXISTS mc_probabilities JSONB;

-- Partial index for the "has MC" query pattern the anti-consensus rule uses
CREATE INDEX IF NOT EXISTS ncaab_game_context_mc_present_idx
  ON public.ncaab_game_context (game_date DESC)
  WHERE mc_probabilities IS NOT NULL;

NOTIFY pgrst, 'reload schema';
