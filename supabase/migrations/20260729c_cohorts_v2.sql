-- 2026-07-29c: cohort v2 family. Adds parallel columns so we can grade v1 vs v2
-- head-to-head over 4 weeks before promoting v2 (or discarding).
--
-- Design intent per user 2026-07-29:
--   "As long as we can still identify patterns between v1 and v2"
-- v1 (signal_confluence_breakdown/_net) stays UNTOUCHED and continues to
-- drive primary_play. v2 runs SHADOW alongside it — same games, different
-- cohort family — and we backtest+monitor before any tier decisions
-- consume it.
--
-- v2 cohort family (7 signals — all backtestable from mlb_game_results):
--   west_east_early      — West Coast team early ET after prior night game
--   cold_after_blowout   — 9+ runs prior day + weak xwOBA rolling
--   series_letdown       — game 3 after winning first 2
--   fresh_vs_grind       — off-day yesterday vs 3-in-3
--   ace_hot_offense      — top-quartile pitcher + hot lineup
--   starter_rest_extreme — SP on 2-3d fatigue or 7+d rust
--   bp_fatigue_combo     — pen taxed 3+ IP last 3d vs opposing high-ERA starter
--
-- Shape of signal_confluence_v2_breakdown:
--   {
--     "west_east_early":      {"side": "home"|"away", "triggered": true, "note": "…"},
--     "cold_after_blowout":   {"side": "home"|"away", "triggered": true, "note": "…"},
--     ...
--     "not_fired": ["ace_hot_offense","starter_rest_extreme"]
--   }
--
-- signal_confluence_v2_net = sum of +1 (home lean) / -1 (away lean) across fired cohorts.

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS signal_confluence_v2_breakdown jsonb,
    ADD COLUMN IF NOT EXISTS signal_confluence_v2_net       integer;

-- Also add to mlb_game_results so historical backtest can persist cohort trigger
-- state per game (useful for grading + audit trails).
ALTER TABLE mlb_game_results
    ADD COLUMN IF NOT EXISTS cohort_v2_breakdown jsonb,
    ADD COLUMN IF NOT EXISTS cohort_v2_net       integer;

COMMENT ON COLUMN mlb_game_context.signal_confluence_v2_breakdown IS
    'v2 cohort family (shadow of v1). Populated by compute_cohorts_v2.py during nightly cron.';
COMMENT ON COLUMN mlb_game_context.signal_confluence_v2_net IS
    'v2 net (+home / -away). Compare vs v1 signal_confluence_net for head-to-head hit rate over rolling window.';

NOTIFY pgrst, 'reload schema';
