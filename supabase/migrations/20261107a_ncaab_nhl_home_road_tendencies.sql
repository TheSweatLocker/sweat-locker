-- NCAAB + NHL home/road tendency matviews (2026-11-07).
--
-- WHY
-- Andy, on a college card: "Purdue game of collge has mismatch purdue +21
-- but purdue -19.5 in pre game jerry read and no sitaional records."
--
-- The missing situational records were not a data gap. GameDetailV2's
-- TeamTendenciesCard maps a sport to a view:
--     NFL   -> nfl_team_home_road_tendencies
--     NCAAF -> ncaaf_team_home_road_tendencies
--     NBA   -> nba_team_home_road_tendencies
--     everything else -> null, and `if (!viewName) return null`
-- so on NCAAB and NHL the panel could never render at all. 20260826b built
-- three of the five live sports and the other two were never added.
--
-- The inputs were there the whole time, measured 2026-11-07:
--     ncaab_game_results  5,152 rows with close_spread AND home_score,
--                         355 teams, median 30 games per team,
--                         spread_result home_covered 2,563 /
--                         away_covered 2,582 / push 7
--     nhl_game_results    2,781 rows with close_puckline AND home_score
--
-- TWO SPORTS, TWO SEASON TYPES -- READ OFF THE LIVE TABLES, NOT ASSUMED.
-- The app looks a team up by (team, season), so the view's season column has
-- to be the same TYPE the app sends, and the two sports disagree:
--   * ncaab_game_results.season is TEXT split-year ('2024-25', '2026-27')
--     and ncaab_game_context.season matches it. Carried through as-is.
--   * nhl_game_results has NO season column, and nhl_game_context.season is
--     an INTEGER (2026 on every game from 2026-09 through 2026-11). So NHL's
--     is DERIVED as the calendar year the season opened in, which is the
--     convention that makes seasonLabel(2026,'NHL') render '2026-27' and
--     that _seasonKey passes through unchanged. The month cutoff is >= 9,
--     copied from 20260901d_team_recent_games_all_sports.sql, which already
--     derives NHL's season from game_date the same way. The repo must not
--     hold two rules for one sport's season
--     (feedback_each_scraper_invented_its_own_season) -- and for NHL, which
--     plays no July or August games, 7 and 9 give identical answers anyway,
--     so matching the existing rule costs nothing.
-- Getting this wrong is not a visible error, it is a silent zero-match --
-- exactly the NBA bug fixed in the app the same day, where a TEXT '2026-27'
-- season was queried as the NUMBER 2026 and the panel rendered nothing.
--
-- Column list is identical to the NBA view so the app renders one component.
-- Idempotent: DROP + CREATE, so re-applying is safe.


-- =======================================================================
-- NCAAB  -- season is TEXT ('2026-27'), carried straight through
-- =======================================================================

DROP MATERIALIZED VIEW IF EXISTS public.ncaab_team_home_road_tendencies;

CREATE MATERIALIZED VIEW public.ncaab_team_home_road_tendencies AS
WITH team_games AS (
  SELECT
    home_team AS team, season, 'home' AS venue,
    (close_spread < 0) AS is_fav,
    home_win AS won,
    spread_result, total_result
  FROM public.ncaab_game_results
  WHERE close_spread IS NOT NULL AND home_score IS NOT NULL

  UNION ALL

  SELECT
    away_team AS team, season, 'road' AS venue,
    (close_spread > 0) AS is_fav,     -- away favored when the home line is positive
    (NOT home_win) AS won,
    spread_result, total_result
  FROM public.ncaab_game_results
  WHERE close_spread IS NOT NULL AND home_score IS NOT NULL
)
SELECT
  team, season,
  COUNT(*) FILTER (WHERE venue='home' AND spread_result='home_covered') AS home_ats_wins,
  COUNT(*) FILTER (WHERE venue='home' AND spread_result='away_covered') AS home_ats_losses,
  COUNT(*) FILTER (WHERE venue='home' AND spread_result='push')         AS home_ats_pushes,
  COUNT(*) FILTER (WHERE venue='road' AND spread_result='away_covered') AS road_ats_wins,
  COUNT(*) FILTER (WHERE venue='road' AND spread_result='home_covered') AS road_ats_losses,
  COUNT(*) FILTER (WHERE venue='road' AND spread_result='push')         AS road_ats_pushes,
  COUNT(*) FILTER (WHERE venue='home' AND won IS TRUE)  AS home_su_wins,
  COUNT(*) FILTER (WHERE venue='home' AND won IS FALSE) AS home_su_losses,
  COUNT(*) FILTER (WHERE venue='road' AND won IS TRUE)  AS road_su_wins,
  COUNT(*) FILTER (WHERE venue='road' AND won IS FALSE) AS road_su_losses,
  COUNT(*) FILTER (WHERE venue='home' AND total_result='over')  AS home_ou_overs,
  COUNT(*) FILTER (WHERE venue='home' AND total_result='under') AS home_ou_unders,
  COUNT(*) FILTER (WHERE venue='home' AND total_result='push')  AS home_ou_pushes,
  COUNT(*) FILTER (WHERE venue='road' AND total_result='over')  AS road_ou_overs,
  COUNT(*) FILTER (WHERE venue='road' AND total_result='under') AS road_ou_unders,
  COUNT(*) FILTER (WHERE venue='road' AND total_result='push')  AS road_ou_pushes,
  COUNT(*) FILTER (WHERE is_fav IS TRUE  AND (
    (venue='home' AND spread_result='home_covered')
 OR (venue='road' AND spread_result='away_covered'))) AS as_fav_ats_wins,
  COUNT(*) FILTER (WHERE is_fav IS TRUE  AND (
    (venue='home' AND spread_result='away_covered')
 OR (venue='road' AND spread_result='home_covered'))) AS as_fav_ats_losses,
  COUNT(*) FILTER (WHERE is_fav IS FALSE AND (
    (venue='home' AND spread_result='home_covered')
 OR (venue='road' AND spread_result='away_covered'))) AS as_dog_ats_wins,
  COUNT(*) FILTER (WHERE is_fav IS FALSE AND (
    (venue='home' AND spread_result='away_covered')
 OR (venue='road' AND spread_result='home_covered'))) AS as_dog_ats_losses,
  COUNT(*) FILTER (WHERE venue='home') AS home_games,
  COUNT(*) FILTER (WHERE venue='road') AS road_games,
  NOW() AS refreshed_at
FROM team_games
GROUP BY team, season;

CREATE UNIQUE INDEX IF NOT EXISTS idx_ncaab_team_home_road_tendencies_uq
  ON public.ncaab_team_home_road_tendencies (team, season);


-- =======================================================================
-- NHL  -- "ATS" is the PUCK LINE here; season derived from game_date
-- =======================================================================
-- close_puckline is +/-1.5 on 2,780 of the 2,781 usable rows (one 0.0), so
-- the fav/dog split is the usual -1.5 / +1.5 and reads like a spread.

DROP MATERIALIZED VIEW IF EXISTS public.nhl_team_home_road_tendencies;

CREATE MATERIALIZED VIEW public.nhl_team_home_road_tendencies AS
WITH team_games AS (
  SELECT
    home_team AS team,
    (CASE WHEN EXTRACT(MONTH FROM game_date) >= 9
          THEN EXTRACT(YEAR FROM game_date)
          ELSE EXTRACT(YEAR FROM game_date) - 1 END)::INT AS season,
    'home' AS venue,
    (close_puckline < 0) AS is_fav,
    home_win AS won,
    spread_result, total_result
  FROM public.nhl_game_results
  WHERE close_puckline IS NOT NULL AND home_score IS NOT NULL

  UNION ALL

  SELECT
    away_team AS team,
    (CASE WHEN EXTRACT(MONTH FROM game_date) >= 9
          THEN EXTRACT(YEAR FROM game_date)
          ELSE EXTRACT(YEAR FROM game_date) - 1 END)::INT AS season,
    'road' AS venue,
    (close_puckline > 0) AS is_fav,
    (NOT home_win) AS won,
    spread_result, total_result
  FROM public.nhl_game_results
  WHERE close_puckline IS NOT NULL AND home_score IS NOT NULL
)
SELECT
  team, season,
  COUNT(*) FILTER (WHERE venue='home' AND spread_result='home_covered') AS home_ats_wins,
  COUNT(*) FILTER (WHERE venue='home' AND spread_result='away_covered') AS home_ats_losses,
  COUNT(*) FILTER (WHERE venue='home' AND spread_result='push')         AS home_ats_pushes,
  COUNT(*) FILTER (WHERE venue='road' AND spread_result='away_covered') AS road_ats_wins,
  COUNT(*) FILTER (WHERE venue='road' AND spread_result='home_covered') AS road_ats_losses,
  COUNT(*) FILTER (WHERE venue='road' AND spread_result='push')         AS road_ats_pushes,
  COUNT(*) FILTER (WHERE venue='home' AND won IS TRUE)  AS home_su_wins,
  COUNT(*) FILTER (WHERE venue='home' AND won IS FALSE) AS home_su_losses,
  COUNT(*) FILTER (WHERE venue='road' AND won IS TRUE)  AS road_su_wins,
  COUNT(*) FILTER (WHERE venue='road' AND won IS FALSE) AS road_su_losses,
  COUNT(*) FILTER (WHERE venue='home' AND total_result='over')  AS home_ou_overs,
  COUNT(*) FILTER (WHERE venue='home' AND total_result='under') AS home_ou_unders,
  COUNT(*) FILTER (WHERE venue='home' AND total_result='push')  AS home_ou_pushes,
  COUNT(*) FILTER (WHERE venue='road' AND total_result='over')  AS road_ou_overs,
  COUNT(*) FILTER (WHERE venue='road' AND total_result='under') AS road_ou_unders,
  COUNT(*) FILTER (WHERE venue='road' AND total_result='push')  AS road_ou_pushes,
  COUNT(*) FILTER (WHERE is_fav IS TRUE  AND (
    (venue='home' AND spread_result='home_covered')
 OR (venue='road' AND spread_result='away_covered'))) AS as_fav_ats_wins,
  COUNT(*) FILTER (WHERE is_fav IS TRUE  AND (
    (venue='home' AND spread_result='away_covered')
 OR (venue='road' AND spread_result='home_covered'))) AS as_fav_ats_losses,
  COUNT(*) FILTER (WHERE is_fav IS FALSE AND (
    (venue='home' AND spread_result='home_covered')
 OR (venue='road' AND spread_result='away_covered'))) AS as_dog_ats_wins,
  COUNT(*) FILTER (WHERE is_fav IS FALSE AND (
    (venue='home' AND spread_result='away_covered')
 OR (venue='road' AND spread_result='home_covered'))) AS as_dog_ats_losses,
  COUNT(*) FILTER (WHERE venue='home') AS home_games,
  COUNT(*) FILTER (WHERE venue='road') AS road_games,
  NOW() AS refreshed_at
FROM team_games
GROUP BY team, season;

CREATE UNIQUE INDEX IF NOT EXISTS idx_nhl_team_home_road_tendencies_uq
  ON public.nhl_team_home_road_tendencies (team, season);


-- =======================================================================
-- Extend the refresh helper. 20260826b's CASE raises on an unknown sport,
-- so a refresh_home_road_tendencies('NCAAB') call would have failed loudly
-- -- which is why no pipeline ever made one.
-- =======================================================================

CREATE OR REPLACE FUNCTION public.refresh_home_road_tendencies(p_sport TEXT)
RETURNS VOID
LANGUAGE plpgsql
AS $fn$
BEGIN
  CASE UPPER(p_sport)
    WHEN 'NFL'   THEN REFRESH MATERIALIZED VIEW CONCURRENTLY public.nfl_team_home_road_tendencies;
    WHEN 'NCAAF' THEN REFRESH MATERIALIZED VIEW CONCURRENTLY public.ncaaf_team_home_road_tendencies;
    WHEN 'NBA'   THEN REFRESH MATERIALIZED VIEW CONCURRENTLY public.nba_team_home_road_tendencies;
    WHEN 'NCAAB' THEN REFRESH MATERIALIZED VIEW CONCURRENTLY public.ncaab_team_home_road_tendencies;
    WHEN 'NHL'   THEN REFRESH MATERIALIZED VIEW CONCURRENTLY public.nhl_team_home_road_tendencies;
    ELSE RAISE EXCEPTION 'refresh_home_road_tendencies: unknown sport %', p_sport;
  END CASE;
END $fn$;

GRANT SELECT ON public.ncaab_team_home_road_tendencies TO anon, authenticated;
GRANT SELECT ON public.nhl_team_home_road_tendencies   TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
