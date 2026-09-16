-- 2026-09-16 · Kill prior-season blend for NFL + NCAAF
--
-- Andy directive: "this season stats populate only now, no more blended".
--
-- team_situational_records + team_stats_rolling are MATERIALIZED VIEWS,
-- rebuilt from historical game_results tables on every REFRESH. That
-- means a straight DELETE would be undone on the next refresh cycle.
-- Solution: rename the underlying MV, expose a filtered VIEW under the
-- original name. Client queries `team_situational_records` (now a view)
-- and gets ONLY 2026 rows for NFL/NCAAF, all seasons for other sports.
-- Refreshes still repopulate the underlying MV — the view keeps
-- filtering. Indexes on the MV still get used via predicate pushdown.
--
-- Coverage check (verified 2026-09-16):
--   NFL:   32/32 teams have 2026 rows in team_situational_records
--   NCAAF: 223/223 teams have 2026 rows
-- No team goes empty; Wk 1-2 users see n=1..2 samples instead of last
-- year's fallback.
--
-- Client behavior (no code change required):
--   - SituationalCard: fetches season=2026; prior-season fallback
--     branch fires (prev fetch also returns 0 for NFL/NCAAF via this
--     view) and finds empty → renders current-season data with no
--     "2025 shown" badge.
--   - TeamStatsCard: fetches .in('season', [2026, 2025]); the 2025
--     side returns 0 via this view; merge = current-only.
--
-- Other sports (MLB / NBA / NCAAB / NHL / UFC) keep their prior-season
-- fallback path intact — they are opted into the filter individually.

BEGIN;

-- ═══ team_situational_records ═══
ALTER MATERIALIZED VIEW public.team_situational_records
  RENAME TO team_situational_records_full;

CREATE OR REPLACE VIEW public.team_situational_records AS
SELECT *
FROM public.team_situational_records_full
WHERE NOT (sport IN ('NFL', 'NCAAF') AND season < 2026);

-- ═══ team_stats_rolling ═══
ALTER MATERIALIZED VIEW public.team_stats_rolling
  RENAME TO team_stats_rolling_full;

CREATE OR REPLACE VIEW public.team_stats_rolling AS
SELECT *
FROM public.team_stats_rolling_full
WHERE NOT (sport IN ('NFL', 'NCAAF') AND season < 2026);

COMMIT;

NOTIFY pgrst, 'reload schema';

-- Verification queries (run after migration):
-- SELECT sport, season, COUNT(*) FROM team_situational_records
--   WHERE sport IN ('NFL','NCAAF') GROUP BY sport, season ORDER BY sport, season;
-- Expected: only 2026 rows for NFL + NCAAF
--
-- SELECT sport, season, COUNT(*) FROM team_situational_records
--   WHERE sport = 'MLB' GROUP BY sport, season ORDER BY season;
-- Expected: MLB retains ALL seasons unchanged
