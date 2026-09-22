-- 2026-09-22 · Point the refresh functions at the renamed matviews
--
-- Andy, 09-22: "some teams like SEA and NE just have 1-0" on the NFL
-- games tab when every team has played two games.
--
-- Not a display bug and not a missing-data bug. nfl_game_results holds
-- all 32 scored games for 2026 — 2 per team, verified. The situational
-- records the badge reads were showing 1 per team for ALL 32 teams,
-- which is Week 1 and nothing since.
--
-- WHY. Migration 20260916a killed the prior-season blend by renaming
-- the materialized views and exposing filtered VIEWS under the original
-- names:
--
--     team_situational_records  (MV)   -> team_situational_records_full
--     team_situational_records  (VIEW) -> SELECT * FROM ..._full WHERE ...
--
-- The refresh functions from 20260901b / 20260901c were not updated.
-- They still say
--
--     REFRESH MATERIALIZED VIEW CONCURRENTLY public.team_situational_records
--
-- which now names a plain view, so every call fails with
--
--     42809: "team_situational_records" is not a table or materialized view
--
-- The rename was correct and the filter works. What broke is the thing
-- that keeps the data underneath it current, and it broke silently:
-- the view kept serving Week 1 rows rather than erroring, so nothing
-- downstream had any reason to complain for six days.
--
-- Two separate failures stacked here, and both are fixed:
--   1. The functions named the wrong object (this migration).
--   2. The 12 workflow steps that call them threw the answer away.
--      Six pipelines (NFL, NCAAF, NCAAB, NBA, NHL, MLB-overnight) each
--      POST both RPCs with `curl -s` — no -o, no status check — under
--      `continue-on-error: true`. A 400 and a 204 are the same event to
--      that step. Every sport's situational + rolling rollups have been
--      frozen since 09-16 and nothing anywhere said so. Fixed in the
--      same commit as this migration.
--
-- 20260916f already refreshes team_stats_rolling_full correctly — it
-- was written after the rename. These two predate it.

BEGIN;

CREATE OR REPLACE FUNCTION public.refresh_team_situational_records()
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY public.team_situational_records_full;
END $$;

CREATE OR REPLACE FUNCTION public.refresh_team_stats_rolling()
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY public.team_stats_rolling_full;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';

-- VERIFY (expect 2 ATS games for every NFL team after Week 2):
--   SELECT public.refresh_team_situational_records();
--   SELECT team, wins, losses, pushes
--     FROM team_situational_records
--    WHERE sport='NFL' AND market='spread' AND filter='overall'
--    ORDER BY team;
