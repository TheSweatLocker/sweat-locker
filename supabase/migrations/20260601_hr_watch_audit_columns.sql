-- 2026-06-01 — HR Watch audit + signal stack columns.
--
-- User direction 6/1: "Yes ship audit. Want to improve it and keep track on
-- how it's doing and predicting guys going yard. Make it easier for user to
-- pick HR guys. Probably need to look into the odds portion as well."
--
-- THREE additions to mlb_hr_watch:
--
-- 1. AUDIT RESOLUTION COLUMNS — track whether each predicted batter actually
--    went yard that game. Powers the daily audit_hr_watch.py + the trend
--    queries ("score >= 60 batters hit HR at X% rate vs score 30-40 at Y%").
--
-- 2. FULL SIGNAL STACK — build_hr_watch.py computes 9 signal scores but only
--    persists 5. Storing all of them so the app can render a transparent
--    breakdown ("Schwarber 62 = power 28 + barrel 14 + park 18 + wind 12").
--
-- 3. PROJECTED HR PROB + BOOK ODDS — derive a 0-1 HR probability from
--    regressed HR rate × expected PAs. Book odds populated by the Phase 2-
--    style attach against Odds API batter_home_runs market.

ALTER TABLE mlb_hr_watch
    -- Audit resolution
    ADD COLUMN IF NOT EXISTS hr_hit BOOLEAN,
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS actual_pa INTEGER,
    -- Full signal stack (previously dropped on upload)
    ADD COLUMN IF NOT EXISTS fb_score INTEGER,
    ADD COLUMN IF NOT EXISTS platoon_score INTEGER,
    ADD COLUMN IF NOT EXISTS recency_score INTEGER,
    ADD COLUMN IF NOT EXISTS savant_score INTEGER,
    -- Projection + odds attach
    ADD COLUMN IF NOT EXISTS projected_hr_prob NUMERIC,
    ADD COLUMN IF NOT EXISTS book_odds INTEGER,
    ADD COLUMN IF NOT EXISTS book_source TEXT,
    -- Optional: a "due" interaction signal flag for narrative surfacing
    ADD COLUMN IF NOT EXISTS due_signal BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN mlb_hr_watch.hr_hit IS
  'Whether this batter actually hit a HR on the predicted game date. NULL = unresolved (audit job hasnt run yet). Populated by mlb_pipeline/audit_hr_watch.py running daily.';

COMMENT ON COLUMN mlb_hr_watch.projected_hr_prob IS
  '0-1 probability batter hits at least 1 HR in this game. Computed as 1 - (1 - regressed_hr_rate)^expected_pa where expected_pa ~ 4.2 for a starter. Used by app for user-facing prob display + comparison vs book implied odds.';

COMMENT ON COLUMN mlb_hr_watch.book_odds IS
  'American odds (+450, +650, etc.) for this batter to hit a HR, attached from Odds API batter_home_runs market. Phase 2-style attach with NULL fallback when unavailable.';

NOTIFY pgrst, 'reload schema';
