-- Ladder soft-scoring (2026-08-19).
--
-- Prior ladder engine required ALL 5 gates (tier, win_prob, consensus,
-- edge, cohort) to pass — an absurdly high bar that left the ladder empty
-- for weeks. Rewrote the engine to SOFT SCORING: count gates passed, take
-- the highest-scoring pick, must clear ≥3 of 5 (matches "3 of 5 gates" UI
-- copy). This adds the gates_passed column so the row records how many
-- gates the picker cleared, for both audit + UI display.
--
-- Backfills existing rows with NULL (unknown) — new rows onward carry
-- the score.

ALTER TABLE public.ladder_rung
  ADD COLUMN IF NOT EXISTS gates_passed INT;

COMMENT ON COLUMN public.ladder_rung.gates_passed IS
  'Number of ladder qualifier gates passed (0-5). Must be ≥3 to fire. '
  'Gates: tier ∈ PRIME/STRONG/LEAN, MC win_prob ≥ 58, consensus ≥ 3/5, '
  'edge ≥ 6pp, cohort ≥ 55% n≥25.';

NOTIFY pgrst, 'reload schema';
