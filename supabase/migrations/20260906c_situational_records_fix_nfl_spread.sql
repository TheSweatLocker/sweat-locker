-- 2026-09-06 team_situational_records: fix NFL missing + spread value case
-- ====================================================================
-- Two bugs blocking Situational Records card on all game detail pages:
--
--   Bug 1: spread_result value mismatch. Migration expected the strings
--          'HOME_COVER' / 'AWAY_COVER' / 'PUSH'. Actual DB values are
--          'home_covered' / 'away_covered' / 'push'. CASE never matched
--          so spread_res was always NULL → seq_spread empty → 0 spread
--          rows in matview → app card silently hid.
--
--   Bug 2: NFL not in matview. Only MLB + NCAAF had UNION clauses.
--          nfl_game_results has 900+ rows with real spread_result data
--          but zero landed in team_situational_records → NFL games
--          silently hid the card entirely.
--
-- Fix: replace CASE literals with LOWER(spread_result) IN ('home_covered'...)
-- matching the actual data + add NFL UNION clauses.
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
      WHEN LOWER(spread_result) IN ('home_covered', 'home_cover') THEN 'won'
      WHEN LOWER(spread_result) IN ('away_covered', 'away_cover') THEN 'lost'
      WHEN LOWER(spread_result) = 'push' THEN 'push'
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
      WHEN LOWER(spread_result) IN ('away_covered', 'away_cover') THEN 'won'
      WHEN LOWER(spread_result) IN ('home_covered', 'home_cover') THEN 'lost'
      WHEN LOWER(spread_result) = 'push' THEN 'push'
      ELSE NULL END,
    CASE
      WHEN home_win IS FALSE THEN 'won'
      WHEN home_win IS TRUE THEN 'lost'
      ELSE NULL END,
    LOWER(total_result)
  FROM public.mlb_game_results
  WHERE home_score IS NOT NULL

  UNION ALL
  -- NCAAF (case-normalized)
  SELECT
    'NCAAF', home_team, season, game_id, game_date, TRUE,
    (CASE WHEN close_spread IS NULL THEN NULL
          WHEN close_spread < 0 THEN TRUE
          WHEN close_spread > 0 THEN FALSE END),
    CASE
      WHEN LOWER(spread_result) IN ('home_covered', 'home_cover') THEN 'won'
      WHEN LOWER(spread_result) IN ('away_covered', 'away_cover') THEN 'lost'
      WHEN LOWER(spread_result) = 'push' THEN 'push'
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
      WHEN LOWER(spread_result) IN ('away_covered', 'away_cover') THEN 'won'
      WHEN LOWER(spread_result) IN ('home_covered', 'home_cover') THEN 'lost'
      WHEN LOWER(spread_result) = 'push' THEN 'push'
      ELSE NULL END,
    CASE
      WHEN home_win IS FALSE THEN 'won'
      WHEN home_win IS TRUE THEN 'lost'
      ELSE NULL END,
    LOWER(total_result)
  FROM public.ncaaf_game_results
  WHERE home_score IS NOT NULL AND COALESCE(neutral_site, FALSE) = FALSE

  UNION ALL
  -- 2026-09-06 NEW: NFL (was completely missing — 900+ nfl_game_results
  -- rows never landed in matview → NFL game detail card silently hid)
  SELECT
    'NFL', home_team, season, game_id, game_date, TRUE,
    (CASE WHEN close_spread IS NULL THEN NULL
          WHEN close_spread > 0 THEN TRUE   -- NFL convention: positive = home fav
          WHEN close_spread < 0 THEN FALSE END),
    CASE
      WHEN LOWER(spread_result) IN ('home_covered', 'home_cover') THEN 'won'
      WHEN LOWER(spread_result) IN ('away_covered', 'away_cover') THEN 'lost'
      WHEN LOWER(spread_result) = 'push' THEN 'push'
      ELSE NULL END,
    CASE
      WHEN home_win IS TRUE THEN 'won'
      WHEN home_win IS FALSE THEN 'lost'
      ELSE NULL END,
    LOWER(total_result)
  FROM public.nfl_game_results
  WHERE home_score IS NOT NULL
  UNION ALL
  SELECT
    'NFL', away_team, season, game_id, game_date, FALSE,
    (CASE WHEN close_spread IS NULL THEN NULL
          WHEN close_spread < 0 THEN TRUE   -- inverse of home is_fav
          WHEN close_spread > 0 THEN FALSE END),
    CASE
      WHEN LOWER(spread_result) IN ('away_covered', 'away_cover') THEN 'won'
      WHEN LOWER(spread_result) IN ('home_covered', 'home_cover') THEN 'lost'
      WHEN LOWER(spread_result) = 'push' THEN 'push'
      ELSE NULL END,
    CASE
      WHEN home_win IS FALSE THEN 'won'
      WHEN home_win IS TRUE THEN 'lost'
      ELSE NULL END,
    LOWER(total_result)
  FROM public.nfl_game_results
  WHERE home_score IS NOT NULL
),

-- PER-MARKET RECENCY: rank each market separately so L10 = last 10
-- games where the market's result was actually populated.
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
  UNION ALL
  SELECT sport, team, season, 'total', 'as_fav',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_total WHERE is_fav GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'as_dog',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_total WHERE NOT is_fav GROUP BY sport, team, season
)
SELECT * FROM agg;

CREATE UNIQUE INDEX IF NOT EXISTS idx_team_situational_records_uq
  ON public.team_situational_records (sport, team, season, market, filter);

-- Recreate the refresh RPC so it still works after the DROP CASCADE
CREATE OR REPLACE FUNCTION public.refresh_team_situational_records()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY public.team_situational_records;
END;
$$;

GRANT EXECUTE ON FUNCTION public.refresh_team_situational_records() TO authenticated, anon;

-- Initial refresh so data lands immediately
REFRESH MATERIALIZED VIEW public.team_situational_records;

NOTIFY pgrst, 'reload schema';
