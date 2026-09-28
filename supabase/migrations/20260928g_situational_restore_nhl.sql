-- 2026-09-28g - Restore NHL to team_situational_records.
--
-- THE DRIFT
-- 20260901e added NHL, NBA and NCAAB blocks "so the SituationalCard renders
-- for every sport as soon as the sport goes live". Two later migrations then
-- did DROP MATERIALIZED VIEW ... CASCADE plus a full CREATE carrying only
-- MLB, NCAAF and NFL:
--
--   20260905a_situational_l10_per_market.sql
--   20260906c_situational_records_fix_nfl_spread.sql
--
-- Neither names NHL, NBA or NCAAB anywhere. A full CREATE replaces the WHOLE
-- definition, so all three were silently dropped, and it went unnoticed for
-- three weeks because none of them was in season. Confirmed live:
-- team_situational_records_full holds only NCAAF, MLB and NFL; NHL is 0 rows,
-- which is why the Situational Records card was blank on every NHL game.
--
-- HOW THIS FILE WAS BUILT, AND WHY THAT MATTERS
-- The MLB / NCAAF / NFL source blocks and the ENTIRE aggregation tail are
-- copied byte-for-byte from 20260906c. Only the three missing sports are
-- inserted. A first attempt rewrote the aggregation by hand and got it wrong
-- in a way that would have regressed the three working sports: the real
-- definition sequences EACH MARKET SEPARATELY (seq_spread / seq_ml /
-- seq_total) so L5 and L10 count the last 5 or 10 games in which THAT market
-- actually resolved - the entire point of 20260905a - while the rewrite used
-- one shared row number across all games. Restoring three dormant sports is
-- not worth breaking three live ones.
--
-- NHL ONLY. NBA AND NCAAB ARE DELIBERATELY LEFT OUT.
-- The first version of this migration restored all three and Postgres
-- rejected it: "UNION types integer and text cannot be matched". The clash is
-- SEASON, not game_id. mlb/ncaaf/nfl_game_results store season as an INTEGER
-- (2026); nba_game_results and ncaab_game_results store TEXT ('2024-25'); and
-- nhl_game_results has no season column at all, which is why it is derived
-- here as an integer.
--
-- That is not a cast away from working, because the app has to find the rows
-- afterwards. nhl_game_context.season is 2026 (integer) and lines up exactly.
-- But nba_game_context.season is '2026-27' (text), so an integer column would
-- never match the value the SituationalCard queries with, while a text column
-- would break the four sports that use integers. Picking one silently would
-- ship a card that looks populated and matches nothing.
--
-- So NBA and NCAAB stay out until that season-format question is settled on
-- purpose. NBA opens 2026-10-21 and NCAAB 2026-11-03; both still need this,
-- and both need the format decision first.
--
-- WHAT THIS CHANGES TODAY: NOTHING VISIBLE FOR NHL, DELIBERATELY
-- Every scored 2026 NHL game is preseason (65 of 65 carry game-type digits
-- '01'), and exhibitions have no posted lines - spread_result is present on
-- 4 of those 65. So the NHL block EXCLUDES PRESEASON and the card stays empty
-- until real games decide something, rather than presenting exhibition
-- records as fact. Same judgement as removing the preseason-derived NHL
-- strength of schedule earlier today.

BEGIN;

-- The filtering view from 20260916a depends on the matview. Drop it first and
-- restore it at the end, or CASCADE removes it with no replacement.
DROP VIEW IF EXISTS public.team_situational_records;
DROP MATERIALIZED VIEW IF EXISTS public.team_situational_records_full CASCADE;
DROP MATERIALIZED VIEW IF EXISTS public.team_situational_records CASCADE;

CREATE MATERIALIZED VIEW public.team_situational_records_full AS
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
  -- NHL - RESTORED 2026-09-28. nhl_game_results has no season column, so
  -- season is derived from game_date. PRESEASON EXCLUDED: digits 4-6 of an
  -- NHL game id are the type (01 pre, 02 regular, 03 playoff) and only 02/03
  -- count. All 65 scored 2026 NHL games are exhibitions, which would have
  -- produced records nobody should act on.
  -- is_fav comes from the MONEYLINE: the puck line is fixed at +/-1.5, so its
  -- sign says nothing about who is favoured.
  UNION ALL
  SELECT
    'NHL' AS sport, home_team AS team,
    CASE WHEN EXTRACT(MONTH FROM game_date) >= 9
         THEN EXTRACT(YEAR FROM game_date)::INT
         ELSE (EXTRACT(YEAR FROM game_date) - 1)::INT END AS season,
    game_id, game_date,
    TRUE AS is_home,
    (CASE WHEN close_home_ml IS NULL OR close_away_ml IS NULL THEN NULL
          ELSE close_home_ml < close_away_ml END) AS is_fav,
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
  FROM public.nhl_game_results
  WHERE home_score IS NOT NULL
    AND SUBSTRING(game_id::TEXT FROM 5 FOR 2) IN ('02', '03')
  UNION ALL
  SELECT
    'NHL', away_team,
    CASE WHEN EXTRACT(MONTH FROM game_date) >= 9
         THEN EXTRACT(YEAR FROM game_date)::INT
         ELSE (EXTRACT(YEAR FROM game_date) - 1)::INT END,
    game_id, game_date,
    FALSE,
    (CASE WHEN close_home_ml IS NULL OR close_away_ml IS NULL THEN NULL
          ELSE close_away_ml < close_home_ml END),
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
  FROM public.nhl_game_results
  WHERE home_score IS NOT NULL
    AND SUBSTRING(game_id::TEXT FROM 5 FOR 2) IN ('02', '03')

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


-- REFRESH CONCURRENTLY requires a unique index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_tsr_full_key
  ON public.team_situational_records_full (sport, team, season, filter, market);

-- Restore 20260916a's filtering view verbatim.
CREATE OR REPLACE VIEW public.team_situational_records AS
SELECT *
FROM public.team_situational_records_full
WHERE NOT (sport IN ('NFL', 'NCAAF') AND season < 2026);

CREATE OR REPLACE FUNCTION public.refresh_team_situational_records()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $F$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY public.team_situational_records_full;
END;
$F$;

GRANT EXECUTE ON FUNCTION public.refresh_team_situational_records() TO authenticated, anon;

COMMENT ON MATERIALIZED VIEW public.team_situational_records_full IS
  'Long-format W-L-P per (sport, team, season, filter, market). SIX sports: '
  'MLB, NCAAF, NFL and NHL. NHL excludes preseason via game-id type digits. '
  'NBA and NCAAB are ABSENT on purpose: their season column is text '
  '(2024-25) while this column is integer, and nba_game_context.season is '
  'text too, so neither a cast nor a column-type change is correct until that '
  'is settled. WARNING: every migration here does a FULL CREATE, which '
  'replaces the whole definition - 20260905a and 20260906c each dropped '
  'NHL/NBA/NCAAB without mentioning them. Count the sports in any new '
  'definition against this list before shipping it.';

COMMIT;

REFRESH MATERIALIZED VIEW public.team_situational_records_full;

NOTIFY pgrst, 'reload schema';
