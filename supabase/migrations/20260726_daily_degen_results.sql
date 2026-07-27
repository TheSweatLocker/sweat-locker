-- ============================================================
-- Daily Degen result tracking (added 2026-07-26).
-- ============================================================
-- daily_degen table stores the day's 3-5 leg parlay but had no way
-- to track whether it hit. Users have been asking "how often does
-- the Daily Degen hit?" — no data existed to answer.
--
-- Adds:
--   legs_resolved  JSONB  — per-leg outcome array:
--                            [{pick, outcome, actual_value, ...}, ...]
--   result         TEXT   — Win | Loss | Push | Pending
--                            Parlay rules: all legs Win = Win. Any Push
--                            replaces with remaining legs. Any Loss = Loss.
--                            Any Pending & no Loss = Pending.
--   resolved_at    TIMESTAMPTZ — when the resolver ran
-- ============================================================

ALTER TABLE daily_degen
  ADD COLUMN IF NOT EXISTS legs_resolved JSONB,
  ADD COLUMN IF NOT EXISTS result        TEXT,
  ADD COLUMN IF NOT EXISTS resolved_at   TIMESTAMPTZ;

COMMENT ON COLUMN daily_degen.result IS
  'Parlay outcome: Win|Loss|Push|Pending. Set by resolve_daily_degen.py the day after game_date.';

COMMENT ON COLUMN daily_degen.legs_resolved IS
  'Per-leg outcome array. Each entry: {pick, outcome (Win/Loss/Push/Pending), actual_value?, note?}.';

CREATE INDEX IF NOT EXISTS idx_daily_degen_result
  ON daily_degen (result, game_date DESC);

NOTIFY pgrst, 'reload schema';
