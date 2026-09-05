-- 2026-09-05 team_situational_records: L10/L5 must be per-MARKET, not per-season
-- ====================================================================
-- Bug (user-visible): Tigers "L10 ATS 2-4" showed only 6 games total.
-- Cause: seq_desc ranked ALL season games; then WHERE spread_res NOT NULL
--        left only 6 of the last 10 (4 games had NULL spread_res).
-- Fix:   Rank recency PER MARKET (spread_desc / ml_desc / total_desc).
--        L10 spread = last 10 games where spread_res was populated.
--        L10 ml = last 10 games where ml_res was populated. Etc.
--
-- Result: "L10 ATS" always shows exactly 10 real ATS results
-- (or fewer if the team hasn't played 10 spread-tracked games yet,
-- honestly reflecting "as many as we have").
-- ====================================================================

DROP MATERIALIZED VIEW IF EXISTS public.team_situational_records CASCADE;

CREATE MATERIALIZED VIEW public.team_situational_records AS
WITH all_games AS (
  -- MLB
  SELECT
    'MLB' AS sport,
    home_team AS team,
    season,
    game_id,
    game_date,
    TRUE AS is_home,
    (CASE WHEN close_spread IS NULL THEN NULL
          WHEN close_spread < 0 THEN TRUE
          WHEN close_spread > 0 THEN FALSE END) AS is_fav,
    CASE
      WHEN spread_result = 'HOME_COVER' THEN 'won'
      WHEN spread_result = 'AWAY_COVER' THEN 'lost'
      WHEN spread_result = 'PUSH' THEN 'push'
      ELSE NULL END AS spread_res,
    CASE
      WHEN home_win IS TRUE THEN 'won'
      WHEN home_win IS FALSE THEN 'lost'
      ELSE NULL END AS ml_res,
    LOWER(total_result) AS total_res
  FROM public.mlb_game_results
  WHERE home_score IS NOT NULL
  UNION ALL
  SELECT
    'MLB', away_team, season, game_id, game_date, FALSE,
    (CASE WHEN close_spread IS NULL THEN NULL
          WHEN close_spread > 0 THEN TRUE
          WHEN close_spread < 0 THEN FALSE END),
    CASE
      WHEN spread_result = 'AWAY_COVER' THEN 'won'
      WHEN spread_result = 'HOME_COVER' THEN 'lost'
      WHEN spread_result = 'PUSH' THEN 'push'
      ELSE NULL END,
    CASE
      WHEN home_win IS FALSE THEN 'won'
      WHEN home_win IS TRUE THEN 'lost'
      ELSE NULL END,
    LOWER(total_result)
  FROM public.mlb_game_results
  WHERE home_score IS NOT NULL

  UNION ALL
  -- NCAAF
  SELECT
    'NCAAF', home_team, season, game_id, game_date, TRUE,
    (CASE WHEN close_spread IS NULL THEN NULL
          WHEN close_spread < 0 THEN TRUE
          WHEN close_spread > 0 THEN FALSE END),
    CASE
      WHEN spread_result = 'HOME_COVER' THEN 'won'
      WHEN spread_result = 'AWAY_COVER' THEN 'lost'
      WHEN spread_result = 'PUSH' THEN 'push'
      ELSE NULL END,
    CASE
      WHEN home_win IS TRUE THEN 'won'
      WHEN home_win IS FALSE THEN 'lost'
      ELSE NULL END,
    LOWER(total_result)
  FROM public.ncaaf_game_results
  WHERE home_score IS NOT NULL AND COALESCE(neutral_site, FALSE) = FALSE
  UNION ALL
  SELECT
    'NCAAF', away_team, season, game_id, game_date, FALSE,
    (CASE WHEN close_spread IS NULL THEN NULL
          WHEN close_spread > 0 THEN TRUE
          WHEN close_spread < 0 THEN FALSE END),
    CASE
      WHEN spread_result = 'AWAY_COVER' THEN 'won'
      WHEN spread_result = 'HOME_COVER' THEN 'lost'
      WHEN spread_result = 'PUSH' THEN 'push'
      ELSE NULL END,
    CASE
      WHEN home_win IS FALSE THEN 'won'
      WHEN home_win IS TRUE THEN 'lost'
      ELSE NULL END,
    LOWER(total_result)
  FROM public.ncaaf_game_results
  WHERE home_score IS NOT NULL AND COALESCE(neutral_site, FALSE) = FALSE
),

-- 2026-09-05 PER-MARKET RECENCY: rank each market's history separately
-- so L10 = last 10 games where the market's result was actually populated.
-- Prevents "L10 ATS 2-4" showing 6 games because 4 of the last 10 had
-- NULL spread_res.
seq_spread AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY sport, team, season
                               ORDER BY game_date DESC, game_id) AS seq
  FROM all_games WHERE spread_res IS NOT NULL
),
seq_ml AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY sport, team, season
                               ORDER BY game_date DESC, game_id) AS seq
  FROM all_games WHERE ml_res IS NOT NULL
),
seq_total AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY sport, team, season
                               ORDER BY game_date DESC, game_id) AS seq
  FROM all_games WHERE total_res IS NOT NULL
),

-- ═══ Aggregate per (sport, team, season, market, filter) ═══
agg AS (
  -- SPREAD
  SELECT sport, team, season, 'spread'::TEXT AS market, 'overall'::TEXT AS filter,
    COUNT(*) FILTER (WHERE spread_res='won')  AS wins,
    COUNT(*) FILTER (WHERE spread_res='lost') AS losses,
    COUNT(*) FILTER (WHERE spread_res='push') AS pushes
  FROM seq_spread GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'l10',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_spread WHERE seq <= 10 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'l5',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_spread WHERE seq <= 5 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'home',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_spread WHERE is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'road',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_spread WHERE NOT is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'as_fav',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_spread WHERE is_fav GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'as_dog',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_spread WHERE NOT is_fav GROUP BY sport, team, season

  UNION ALL

  -- ML
  SELECT sport, team, season, 'ml', 'overall',
    COUNT(*) FILTER (WHERE ml_res='won'),
    COUNT(*) FILTER (WHERE ml_res='lost'), 0
  FROM seq_ml GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'l10',
    COUNT(*) FILTER (WHERE ml_res='won'),
    COUNT(*) FILTER (WHERE ml_res='lost'), 0
  FROM seq_ml WHERE seq <= 10 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'l5',
    COUNT(*) FILTER (WHERE ml_res='won'),
    COUNT(*) FILTER (WHERE ml_res='lost'), 0
  FROM seq_ml WHERE seq <= 5 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'home',
    COUNT(*) FILTER (WHERE ml_res='won'),
    COUNT(*) FILTER (WHERE ml_res='lost'), 0
  FROM seq_ml WHERE is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'road',
    COUNT(*) FILTER (WHERE ml_res='won'),
    COUNT(*) FILTER (WHERE ml_res='lost'), 0
  FROM seq_ml WHERE NOT is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'as_fav',
    COUNT(*) FILTER (WHERE ml_res='won'),
    COUNT(*) FILTER (WHERE ml_res='lost'), 0
  FROM seq_ml WHERE is_fav GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'as_dog',
    COUNT(*) FILTER (WHERE ml_res='won'),
    COUNT(*) FILTER (WHERE ml_res='lost'), 0
  FROM seq_ml WHERE NOT is_fav GROUP BY sport, team, season

  UNION ALL

  -- TOTAL
  SELECT sport, team, season, 'total', 'overall',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_total GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'l10',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_total WHERE seq <= 10 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'l5',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_total WHERE seq <= 5 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'home',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_total WHERE is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'road',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_total WHERE NOT is_home GROUP BY sport, team, season
)
SELECT sport, team, season, market, filter, wins, losses, pushes
FROM agg
WHERE wins + losses + pushes > 0;

CREATE UNIQUE INDEX IF NOT EXISTS idx_team_situational_records_pk
    ON public.team_situational_records (sport, team, season, market, filter);
CREATE INDEX IF NOT EXISTS idx_team_situational_records_lookup
    ON public.team_situational_records (sport, team, market);

-- Refresh function stays the same
CREATE OR REPLACE FUNCTION public.refresh_team_situational_records()
RETURNS void
LANGUAGE sql SECURITY DEFINER
AS $$
    REFRESH MATERIALIZED VIEW CONCURRENTLY public.team_situational_records;
$$;

-- Initial populate
REFRESH MATERIALIZED VIEW public.team_situational_records;

NOTIFY pgrst, 'reload schema';
