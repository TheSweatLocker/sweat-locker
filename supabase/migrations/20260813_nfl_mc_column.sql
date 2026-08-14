-- NFL MC probabilities column (2026-08-13).
--
-- Adds mc_probabilities blob to nfl_game_context so the new drive-by-drive
-- Monte Carlo simulator can write win prob + expected margin + expected
-- total per game. Same shape as mlb_game_context.mc_probabilities so
-- downstream consumers (anti-consensus rule, sweat score, primary_play
-- resolver) can read a single field name across sports.
--
-- Shape (JSONB):
--   {
--     "mc_p_home": 0.573,           // 10k-sim home win probability
--     "mc_p_away": 0.427,
--     "mc_expected_margin": 3.2,    // home - away expected pts
--     "mc_expected_total": 44.8,
--     "mc_stddev_margin": 12.4,     // sim variance (informational)
--     "mc_p_over_line": 0.51,       // if close_total present
--     "mc_confidence_high": false,  // true when |margin| > 6 AND stddev < 10
--     "generated_at": "2026-08-13T20:15:00Z"
--   }
--
-- Nullable — populated by nfl_mc_simulator.py post-game_context build.

ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS mc_probabilities JSONB;

-- Partial index for the "has MC" query pattern the anti-consensus rule uses
CREATE INDEX IF NOT EXISTS nfl_game_context_mc_present_idx
  ON public.nfl_game_context (game_date DESC)
  WHERE mc_probabilities IS NOT NULL;

NOTIFY pgrst, 'reload schema';
