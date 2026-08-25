-- Home/road tendency materialized views (2026-08-25).
--
-- Per-team, per-season aggregates for ATS / SU / O/U / as-fav / as-dog
-- broken out by home vs road. Powers the expanded "Trends & Tendencies"
-- card in NFL / NCAAF / NBA slot redesigns (mocked 2026-08-25).
--
-- Design:
--   - Materialized view (not a regular table) because it's derivable from
--     {sport}_game_results and refreshed nightly by each sport's pipeline.
--   - Columns mirror across sports so the app renders one component.
--   - Views expose a wide row per (team, season) — the app reads once and
--     picks home_* fields for the home side, road_* for the away side.
--   - spread_result / total_result are already computed on the results row;
--     we don't recompute cover — just count.
--
-- Refresh:
--   REFRESH MATERIALIZED VIEW CONCURRENTLY nfl_team_home_road_tendencies;
--   (needs a UNIQUE index for CONCURRENTLY — added per view)
--
-- Idempotent — DROP + CREATE at bottom so schema changes re-apply cleanly.


-- ═══════════════════════════════════════════════════════════════════════
-- NFL
-- ═══════════════════════════════════════════════════════════════════════

DROP MATERIALIZED VIEW IF EXISTS public.nfl_team_home_road_tendencies;

CREATE MATERIALIZED VIEW public.nfl_team_home_road_tendencies AS
WITH team_games AS (
  -- Row per (team, season, game) tagged with home_or_away + fav/dog + WL
  SELECT
    home_team AS team,
    season,
    'home'    AS venue,
    (close_spread < 0) AS is_fav,
    home_win  AS won,
    spread_result,
    total_result,
    close_spread,
    close_total,
    total_points
  FROM public.nfl_game_results
  WHERE close_spread IS NOT NULL AND home_score IS NOT NULL

  UNION ALL

  SELECT
    away_team AS team,
    season,
    'road'    AS venue,
    (close_spread > 0) AS is_fav,   -- away favored when spread > 0 (home side neg)
    (NOT home_win) AS won,
    spread_result,
    total_result,
    close_spread,
    close_total,
    total_points
  FROM public.nfl_game_results
  WHERE close_spread IS NOT NULL AND home_score IS NOT NULL
)
SELECT
  team,
  season,
  -- Home ATS
  COUNT(*) FILTER (WHERE venue='home' AND (
    (spread_result='home_covered'))) AS home_ats_wins,
  COUNT(*) FILTER (WHERE venue='home' AND (
    (spread_result='away_covered'))) AS home_ats_losses,
  COUNT(*) FILTER (WHERE venue='home' AND spread_result='push') AS home_ats_pushes,
  -- Road ATS
  COUNT(*) FILTER (WHERE venue='road' AND spread_result='away_covered') AS road_ats_wins,
  COUNT(*) FILTER (WHERE venue='road' AND spread_result='home_covered') AS road_ats_losses,
  COUNT(*) FILTER (WHERE venue='road' AND spread_result='push') AS road_ats_pushes,
  -- Home SU
  COUNT(*) FILTER (WHERE venue='home' AND won IS TRUE) AS home_su_wins,
  COUNT(*) FILTER (WHERE venue='home' AND won IS FALSE) AS home_su_losses,
  -- Road SU
  COUNT(*) FILTER (WHERE venue='road' AND won IS TRUE) AS road_su_wins,
  COUNT(*) FILTER (WHERE venue='road' AND won IS FALSE) AS road_su_losses,
  -- Home O/U
  COUNT(*) FILTER (WHERE venue='home' AND total_result='over')  AS home_ou_overs,
  COUNT(*) FILTER (WHERE venue='home' AND total_result='under') AS home_ou_unders,
  COUNT(*) FILTER (WHERE venue='home' AND total_result='push')  AS home_ou_pushes,
  -- Road O/U
  COUNT(*) FILTER (WHERE venue='road' AND total_result='over')  AS road_ou_overs,
  COUNT(*) FILTER (WHERE venue='road' AND total_result='under') AS road_ou_unders,
  COUNT(*) FILTER (WHERE venue='road' AND total_result='push')  AS road_ou_pushes,
  -- ATS as fav / dog
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
  -- Totals
  COUNT(*) FILTER (WHERE venue='home') AS home_games,
  COUNT(*) FILTER (WHERE venue='road') AS road_games,
  NOW() AS refreshed_at
FROM team_games
GROUP BY team, season;

CREATE UNIQUE INDEX IF NOT EXISTS idx_nfl_team_home_road_tendencies_uq
  ON public.nfl_team_home_road_tendencies (team, season);


-- ═══════════════════════════════════════════════════════════════════════
-- NCAAF
-- ═══════════════════════════════════════════════════════════════════════
-- NCAAF results table: same shape as NFL (home_team/away_team/home_win/
-- spread_result/total_result/close_spread). neutral_site column exists —
-- we treat neutral-site games as neither home nor road (excluded from splits).

DROP MATERIALIZED VIEW IF EXISTS public.ncaaf_team_home_road_tendencies;

CREATE MATERIALIZED VIEW public.ncaaf_team_home_road_tendencies AS
WITH team_games AS (
  SELECT
    home_team AS team, season, 'home' AS venue,
    (close_spread < 0) AS is_fav,
    home_win AS won,
    spread_result, total_result
  FROM public.ncaaf_game_results
  WHERE close_spread IS NOT NULL AND home_score IS NOT NULL
    AND COALESCE(neutral_site, FALSE) = FALSE

  UNION ALL

  SELECT
    away_team AS team, season, 'road' AS venue,
    (close_spread > 0) AS is_fav,
    (NOT home_win) AS won,
    spread_result, total_result
  FROM public.ncaaf_game_results
  WHERE close_spread IS NOT NULL AND home_score IS NOT NULL
    AND COALESCE(neutral_site, FALSE) = FALSE
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_ncaaf_team_home_road_tendencies_uq
  ON public.ncaaf_team_home_road_tendencies (team, season);


-- ═══════════════════════════════════════════════════════════════════════
-- NBA
-- ═══════════════════════════════════════════════════════════════════════
-- NBA results table: home_team / away_team / home_win / spread_result /
-- total_result / close_spread / close_total. No neutral_site flag — NBA
-- reg-season games are all home/road (playoff neutrality rare, ignored).

DROP MATERIALIZED VIEW IF EXISTS public.nba_team_home_road_tendencies;

CREATE MATERIALIZED VIEW public.nba_team_home_road_tendencies AS
WITH team_games AS (
  SELECT
    home_team AS team, season, 'home' AS venue,
    (close_spread < 0) AS is_fav,
    home_win AS won,
    spread_result, total_result
  FROM public.nba_game_results
  WHERE close_spread IS NOT NULL AND home_score IS NOT NULL

  UNION ALL

  SELECT
    away_team AS team, season, 'road' AS venue,
    (close_spread > 0) AS is_fav,
    (NOT home_win) AS won,
    spread_result, total_result
  FROM public.nba_game_results
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_nba_team_home_road_tendencies_uq
  ON public.nba_team_home_road_tendencies (team, season);


-- ═══════════════════════════════════════════════════════════════════════
-- Refresh helper — call per sport from each pipeline's nightly cron.
-- CONCURRENTLY requires the unique index above; keeps reads unblocked
-- during the swap.
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.refresh_home_road_tendencies(p_sport TEXT)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
  CASE UPPER(p_sport)
    WHEN 'NFL'   THEN REFRESH MATERIALIZED VIEW CONCURRENTLY public.nfl_team_home_road_tendencies;
    WHEN 'NCAAF' THEN REFRESH MATERIALIZED VIEW CONCURRENTLY public.ncaaf_team_home_road_tendencies;
    WHEN 'NBA'   THEN REFRESH MATERIALIZED VIEW CONCURRENTLY public.nba_team_home_road_tendencies;
    ELSE RAISE EXCEPTION 'refresh_home_road_tendencies: unknown sport %', p_sport;
  END CASE;
END $$;

NOTIFY pgrst, 'reload schema';
