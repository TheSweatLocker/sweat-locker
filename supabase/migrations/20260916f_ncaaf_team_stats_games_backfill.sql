-- 2026-09-16f · Backfill ncaaf_team_stats.games from ncaaf_game_results
--
-- Andy screenshot audit (Temple/Toledo Wk3 game card): Team Stats
-- section showed "—" for pass_yds_pg, rush_yds_pg, total_yds_pg,
-- turnovers_pg, penalty_yds_pg on both teams. Root cause: the
-- team_stats_rolling matview computes these as raw_value / games,
-- but ncaaf_team_stats.games is NULL on every 2026 row — the CFBD
-- puller doesn't set it. NULL divisor → NULL result → app renders "—".
--
-- Fix:
--   1) Backfill games column from a count of ncaaf_game_results
--      (counts each team as home + away appearances).
--   2) Refresh team_stats_rolling matview.
--
-- Wk3 impact: all volumetric-per-game stats surface immediately once
-- games is set. Temple/Toledo cards will show pass_yds_pg, rush_yds_pg,
-- etc. with real Wk1-2 sample.
--
-- Follow-up (separate PR): patch the CFBD stats puller
-- (ncaaf_stats_pull.py or similar) to include games in the initial
-- upsert so this stays populated going forward.

BEGIN;

-- Compute games played per team from ncaaf_game_results 2026 and
-- update the team_stats row for each. Uses UPDATE ... FROM subquery
-- pattern so it runs in one pass instead of per-team loop.
UPDATE public.ncaaf_team_stats AS ts
   SET games = gc.games_played
  FROM (
    SELECT team, season, COUNT(*)::INT AS games_played
      FROM (
        SELECT home_team AS team, season FROM public.ncaaf_game_results
         WHERE season = 2026 AND home_score IS NOT NULL
        UNION ALL
        SELECT away_team AS team, season FROM public.ncaaf_game_results
         WHERE season = 2026 AND away_score IS NOT NULL
      ) all_games
     GROUP BY team, season
  ) gc
 WHERE ts.team = gc.team
   AND ts.season = gc.season
   AND ts.season = 2026;

-- Refresh the rolling matview so per-game divisions recompute.
REFRESH MATERIALIZED VIEW CONCURRENTLY public.team_stats_rolling_full;

COMMIT;

NOTIFY pgrst, 'reload schema';

-- Verification:
-- SELECT team, games, pass_yards, rush_yards
--   FROM ncaaf_team_stats WHERE team IN ('Temple','Toledo') AND season=2026;
-- Expected: games=3, non-null yards.
--
-- SELECT team, stat_key, raw_value FROM team_stats_rolling
--  WHERE sport='NCAAF' AND team IN ('Temple','Toledo')
--    AND stat_key IN ('pass_yds_pg','rush_yds_pg','total_yds_pg',
--                     'turnovers_pg','penalty_yds_pg');
-- Expected: real numeric values (e.g. Temple pass_yds_pg = 249/3 = 83).
