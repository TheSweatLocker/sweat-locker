-- 2026-09-10: sweat_tier_current — raw tier alongside monotonic-locked sweat_tier.
--
-- User pain: HOU @ PHI 9/10 showed sweat_tier=STRONG on sweat_score=59.
-- Root cause: monotonic-lock design (20260603) — tier peaked at STRONG
-- earlier today, capped at 1-rank-above current LIGHT_LEAN → holds
-- STRONG. Intentional to avoid 6AM STRONG → 2PM LEAN flip-flops, but
-- creates the confusing "STRONG chip on 59-score" UX.
--
-- Fix: add sweat_tier_current for the CURRENTLY-COMPUTED tier (no lock).
-- App can render either:
--   - sweat_tier (existing, monotonic — for "committed at 6AM" callers)
--   - sweat_tier_current (raw — for "what does the model say NOW" chips)
--
-- OR show both: "STRONG (was STRONG at 6AM, LIGHT_LEAN now)"
--
-- Writer (mlb_pipeline/play_of_day.py::write_sweat_score) needs to
-- populate sweat_tier_current alongside sweat_tier — that ships in a
-- follow-up code change referencing this migration.

ALTER TABLE mlb_game_context
  ADD COLUMN IF NOT EXISTS sweat_tier_current text;

ALTER TABLE nfl_game_context
  ADD COLUMN IF NOT EXISTS sweat_tier_current text;

ALTER TABLE ncaaf_game_context
  ADD COLUMN IF NOT EXISTS sweat_tier_current text;

NOTIFY pgrst, 'reload schema';
