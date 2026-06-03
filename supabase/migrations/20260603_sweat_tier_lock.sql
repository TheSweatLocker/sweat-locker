-- 2026-06-03 — Sweat tier monotonic-up lock.
--
-- Problem (recurring 3 weeks running): users see games drop tier intraday.
-- A game scored STRONG at the 6 AM cron, then the 2 PM cron rescores using
-- updated prop counts / live lines / scratched batters and the same scorer
-- emits LIGHT_LEAN. From the user seat this looks like the model "lost
-- conviction" — but the underlying inputs changed, not the model.
--
-- Fix: persist the highest tier the scorer has produced for a game today,
-- plus when it was first reached. write_sweat_score reads sweat_tier_max
-- back and refuses to demote within the same date.
--
-- Scores (the numeric value) still float with the data — that's transparent
-- and matches the audit trail. Only the TIER is monotonic.
--
-- See: project_pm_cron_live_game_prop_overwrite, project_sweat_dimensional_redesign,
-- project_sweat_score_rewrite.

ALTER TABLE mlb_game_context
    -- Highest tier reached by the scorer today (PRIME > STRONG > LIGHT_LEAN > PASS).
    -- Reset at the start of each game_date by virtue of the per-game-id key —
    -- when a new game_date row inserts, this column starts NULL.
    ADD COLUMN IF NOT EXISTS sweat_tier_max TEXT,
    -- Timestamp when sweat_tier_max was first written for this row. Lets the
    -- app render "locked at 6:08 AM" so users see the score was set before
    -- intraday shifts (e.g., lineup scratches) reshuffled the dim drivers.
    ADD COLUMN IF NOT EXISTS sweat_tier_locked_at TIMESTAMPTZ;

COMMENT ON COLUMN mlb_game_context.sweat_tier_max IS
  'Highest tier the scorer has produced for this game on this date. write_sweat_score refuses to demote sweat_tier below this floor within the same game_date — solves the 6 AM STRONG → 2 PM LIGHT_LEAN intraday-regression UX issue.';

COMMENT ON COLUMN mlb_game_context.sweat_tier_locked_at IS
  'When sweat_tier_max was first reached today. App surface label ("locked at 6:08 AM PT") so users see the score is anchored to the morning compute, not a moving target.';

NOTIFY pgrst, 'reload schema';
