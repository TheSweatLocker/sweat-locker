-- team_situational_records — extend to ALL sports (2026-09-01)
--
-- Corrects same scoping miss as 20260901d — adds NFL / NBA / NCAAB / NHL
-- UNION blocks so the SituationalCard renders for every sport as soon
-- as the sport goes live. Idempotent DROP + CREATE.
--
-- Same schema quirks handled as 20260901d:
--   NBA/NCAAB season TEXT → cast to leading-year INT
--   NHL no season → derive from game_date
--   NHL close_puckline → aliased (is_fav via ML sign since puckline
--     is fixed ±1.5)
--   NHL close_home_ml / close_away_ml (NOT ml_close suffix)
--   NCAAB same MLs use home_ml_close / away_ml_close (word-order flip)
--     — not consumed here since is_fav uses close_spread sign
--   NHL is_fav — key off ML sign (close_home_ml < close_away_ml means
--     home is favored, since more negative ML = favored)

DROP MATERIALIZED VIEW IF EXISTS public.team_situational_records;

CREATE MATERIALIZED VIEW public.team_situational_records AS
WITH all_games AS (

  -- ═══ MLB · home / away perspectives ═══
  SELECT
    'MLB'::TEXT AS sport, game_id, season, game_date,
    home_team AS team, TRUE AS is_home,
    close_spread AS spread_line,
    (close_spread IS NOT NULL AND close_spread < 0) AS is_fav,
    home_win AS won_su,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won' WHEN 'away_covered' THEN 'lost'
      WHEN 'push' THEN 'push' ELSE NULL END AS spread_res,
    LOWER(total_result) AS total_res
  FROM public.mlb_game_results WHERE home_score IS NOT NULL
  UNION ALL
  SELECT 'MLB', game_id, season, game_date,
    away_team, FALSE,
    -close_spread,
    (close_spread IS NOT NULL AND close_spread > 0),
    (NOT home_win),
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL END,
    LOWER(total_result)
  FROM public.mlb_game_results WHERE home_score IS NOT NULL

  -- ═══ NCAAF · exclude neutral for H/R splits ═══
  UNION ALL
  SELECT 'NCAAF', game_id, season, game_date,
    home_team, TRUE,
    close_spread,
    (close_spread IS NOT NULL AND close_spread < 0),
    home_win,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won' WHEN 'away_covered' THEN 'lost'
      WHEN 'push' THEN 'push' ELSE NULL END,
    LOWER(total_result)
  FROM public.ncaaf_game_results
  WHERE home_score IS NOT NULL AND COALESCE(neutral_site, FALSE) = FALSE
  UNION ALL
  SELECT 'NCAAF', game_id, season, game_date,
    away_team, FALSE,
    -close_spread,
    (close_spread IS NOT NULL AND close_spread > 0),
    (NOT home_win),
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL END,
    LOWER(total_result)
  FROM public.ncaaf_game_results
  WHERE home_score IS NOT NULL AND COALESCE(neutral_site, FALSE) = FALSE

  -- ═══ NFL ═══
  UNION ALL
  SELECT 'NFL', game_id, season, game_date,
    home_team, TRUE,
    close_spread,
    (close_spread IS NOT NULL AND close_spread < 0),
    home_win,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won' WHEN 'away_covered' THEN 'lost'
      WHEN 'push' THEN 'push' ELSE NULL END,
    LOWER(total_result)
  FROM public.nfl_game_results WHERE home_score IS NOT NULL
  UNION ALL
  SELECT 'NFL', game_id, season, game_date,
    away_team, FALSE,
    -close_spread,
    (close_spread IS NOT NULL AND close_spread > 0),
    (NOT home_win),
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL END,
    LOWER(total_result)
  FROM public.nfl_game_results WHERE home_score IS NOT NULL

  -- ═══ NBA · season TEXT cast ═══
  UNION ALL
  SELECT 'NBA', game_id,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    game_date, home_team, TRUE,
    close_spread,
    (close_spread IS NOT NULL AND close_spread < 0),
    home_win,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won' WHEN 'away_covered' THEN 'lost'
      WHEN 'push' THEN 'push' ELSE NULL END,
    LOWER(total_result)
  FROM public.nba_game_results WHERE home_score IS NOT NULL
  UNION ALL
  SELECT 'NBA', game_id,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    game_date, away_team, FALSE,
    -close_spread,
    (close_spread IS NOT NULL AND close_spread > 0),
    (NOT home_win),
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL END,
    LOWER(total_result)
  FROM public.nba_game_results WHERE home_score IS NOT NULL

  -- ═══ NCAAB · season TEXT cast ═══
  UNION ALL
  SELECT 'NCAAB', game_id,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    game_date, home_team, TRUE,
    close_spread,
    (close_spread IS NOT NULL AND close_spread < 0),
    home_win,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won' WHEN 'away_covered' THEN 'lost'
      WHEN 'push' THEN 'push' ELSE NULL END,
    LOWER(total_result)
  FROM public.ncaab_game_results WHERE home_score IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', game_id,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    game_date, away_team, FALSE,
    -close_spread,
    (close_spread IS NOT NULL AND close_spread > 0),
    (NOT home_win),
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL END,
    LOWER(total_result)
  FROM public.ncaab_game_results WHERE home_score IS NOT NULL

  -- ═══ NHL · derive season; ML-based is_fav (puckline useless: fixed ±1.5) ═══
  UNION ALL
  SELECT 'NHL', game_id,
    CASE WHEN EXTRACT(MONTH FROM game_date) >= 9
         THEN EXTRACT(YEAR FROM game_date)::INT
         ELSE (EXTRACT(YEAR FROM game_date) - 1)::INT END,
    game_date, home_team, TRUE,
    close_puckline,
    (close_home_ml IS NOT NULL AND close_away_ml IS NOT NULL
     AND close_home_ml < close_away_ml),
    home_win,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won' WHEN 'away_covered' THEN 'lost'
      WHEN 'push' THEN 'push' ELSE NULL END,
    LOWER(total_result)
  FROM public.nhl_game_results WHERE home_score IS NOT NULL
  UNION ALL
  SELECT 'NHL', game_id,
    CASE WHEN EXTRACT(MONTH FROM game_date) >= 9
         THEN EXTRACT(YEAR FROM game_date)::INT
         ELSE (EXTRACT(YEAR FROM game_date) - 1)::INT END,
    game_date, away_team, FALSE,
    -close_puckline,
    (close_home_ml IS NOT NULL AND close_away_ml IS NOT NULL
     AND close_away_ml < close_home_ml),
    (NOT home_win),
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL END,
    LOWER(total_result)
  FROM public.nhl_game_results WHERE home_score IS NOT NULL
),
seq_enriched AS (
  SELECT
    *,
    ROW_NUMBER() OVER (PARTITION BY sport, team, season ORDER BY game_date DESC, game_id) AS seq_desc
  FROM all_games
),
agg AS (
  -- SPREAD market
  SELECT sport, team, season, 'spread'::TEXT AS market, 'overall'::TEXT AS filter,
    COUNT(*) FILTER (WHERE spread_res='won') AS wins,
    COUNT(*) FILTER (WHERE spread_res='lost') AS losses,
    COUNT(*) FILTER (WHERE spread_res='push') AS pushes
  FROM seq_enriched WHERE spread_res IS NOT NULL GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'l10',
    COUNT(*) FILTER (WHERE spread_res='won'), COUNT(*) FILTER (WHERE spread_res='lost'), COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND seq_desc <= 10 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'l5',
    COUNT(*) FILTER (WHERE spread_res='won'), COUNT(*) FILTER (WHERE spread_res='lost'), COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND seq_desc <= 5 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'home',
    COUNT(*) FILTER (WHERE spread_res='won'), COUNT(*) FILTER (WHERE spread_res='lost'), COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'road',
    COUNT(*) FILTER (WHERE spread_res='won'), COUNT(*) FILTER (WHERE spread_res='lost'), COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND NOT is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'as_fav',
    COUNT(*) FILTER (WHERE spread_res='won'), COUNT(*) FILTER (WHERE spread_res='lost'), COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND is_fav GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'spread', 'as_dog',
    COUNT(*) FILTER (WHERE spread_res='won'), COUNT(*) FILTER (WHERE spread_res='lost'), COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND NOT is_fav GROUP BY sport, team, season

  -- TOTAL market (wins=OVER)
  UNION ALL
  SELECT sport, team, season, 'total', 'overall',
    COUNT(*) FILTER (WHERE total_res='over'), COUNT(*) FILTER (WHERE total_res='under'), COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'l10',
    COUNT(*) FILTER (WHERE total_res='over'), COUNT(*) FILTER (WHERE total_res='under'), COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND seq_desc <= 10 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'l5',
    COUNT(*) FILTER (WHERE total_res='over'), COUNT(*) FILTER (WHERE total_res='under'), COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND seq_desc <= 5 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'home',
    COUNT(*) FILTER (WHERE total_res='over'), COUNT(*) FILTER (WHERE total_res='under'), COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'road',
    COUNT(*) FILTER (WHERE total_res='over'), COUNT(*) FILTER (WHERE total_res='under'), COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND NOT is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'as_fav',
    COUNT(*) FILTER (WHERE total_res='over'), COUNT(*) FILTER (WHERE total_res='under'), COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND is_fav GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'as_dog',
    COUNT(*) FILTER (WHERE total_res='over'), COUNT(*) FILTER (WHERE total_res='under'), COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND NOT is_fav GROUP BY sport, team, season

  -- ML market
  UNION ALL
  SELECT sport, team, season, 'ml', 'overall',
    COUNT(*) FILTER (WHERE won_su IS TRUE), COUNT(*) FILTER (WHERE won_su IS FALSE), 0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'l10',
    COUNT(*) FILTER (WHERE won_su IS TRUE), COUNT(*) FILTER (WHERE won_su IS FALSE), 0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND seq_desc <= 10 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'l5',
    COUNT(*) FILTER (WHERE won_su IS TRUE), COUNT(*) FILTER (WHERE won_su IS FALSE), 0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND seq_desc <= 5 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'home',
    COUNT(*) FILTER (WHERE won_su IS TRUE), COUNT(*) FILTER (WHERE won_su IS FALSE), 0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'road',
    COUNT(*) FILTER (WHERE won_su IS TRUE), COUNT(*) FILTER (WHERE won_su IS FALSE), 0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND NOT is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'as_fav',
    COUNT(*) FILTER (WHERE won_su IS TRUE), COUNT(*) FILTER (WHERE won_su IS FALSE), 0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND is_fav GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'as_dog',
    COUNT(*) FILTER (WHERE won_su IS TRUE), COUNT(*) FILTER (WHERE won_su IS FALSE), 0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND NOT is_fav GROUP BY sport, team, season
)
SELECT
  sport, team, season, market, filter,
  wins, losses, pushes,
  (wins + losses + pushes) AS games,
  NOW() AS refreshed_at
FROM agg
WHERE (wins + losses + pushes) > 0
  AND season IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_team_situational_records_uq
  ON public.team_situational_records (sport, team, season, market, filter);
CREATE INDEX IF NOT EXISTS idx_team_situational_records_lookup
  ON public.team_situational_records (sport, team, season);

NOTIFY pgrst, 'reload schema';
