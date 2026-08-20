-- Prop Playbook V2 fields (2026-08-20).
--
-- Adds the market-check + recommendation output fields to
-- prop_playbook_decisions. Phase 1 of the Prop Jerry V2 cutover — user
-- reviewed vision + approved:
--
--   1. All prop lines run through playbook (already writes here)
--   2. Playbook aggregates signals → score 0-100 (existing playbook_conviction)
--   3. NEW: market-check gate compares our score vs book-implied prob → edge_pp
--   4. NEW: recommendation label — BACK / LEAN / NO PLAY based on edge_pp + score
--   5. NEW: market_implied_prob for audit + UI display
--
-- Once app is switched to render from these fields (Phase 2), legacy
-- generate_props.py writes stop and Jerry LLM prose stops. Playbook v2
-- becomes the only prop pipeline.
--
-- Recommendation thresholds (per user approval):
--   BACK       edge_pp ≥ +8  AND playbook_conviction ≥ 65
--   LEAN       edge_pp ≥ +3  AND playbook_conviction ≥ 55
--   NO PLAY    else (hidden from card)

ALTER TABLE public.prop_playbook_decisions
  ADD COLUMN IF NOT EXISTS market_implied_prob NUMERIC,   -- 0-100, from book odds
  ADD COLUMN IF NOT EXISTS edge_pp NUMERIC,                -- our_prob − market_implied_prob
  ADD COLUMN IF NOT EXISTS recommendation TEXT;            -- 'BACK' | 'LEAN' | 'NO_PLAY'

CREATE INDEX IF NOT EXISTS ppd_recommendation_idx
  ON public.prop_playbook_decisions (recommendation, game_date DESC)
  WHERE recommendation IN ('BACK', 'LEAN');

COMMENT ON COLUMN public.prop_playbook_decisions.market_implied_prob IS
  'Book-implied probability from best available odds on picked side. 0-100.';
COMMENT ON COLUMN public.prop_playbook_decisions.edge_pp IS
  'Our-model prob minus market-implied prob, in percentage points. Positive = edge.';
COMMENT ON COLUMN public.prop_playbook_decisions.recommendation IS
  'BACK / LEAN / NO_PLAY per user-approved gates. NO_PLAY hidden from card.';

NOTIFY pgrst, 'reload schema';
