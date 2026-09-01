-- team_recent_games — extend to ALL sports (2026-09-01)
--
-- Corrects the scoping miss in 20260901_team_recent_games_matview.sql:
-- initial ship covered MLB + NCAAF only because those were the two live
-- test sports. Per feedback_test_sports_not_scope_limit, the whole point
-- of the `sport` column is cross-sport uniformity — new sport onboarding
-- should be ZERO engineering. Adding NFL / NBA / NCAAB / NHL UNION blocks
-- so all live/upcoming sports populate the matview immediately.
--
-- Per-sport source-schema quirks handled:
--   NBA / NCAAB   — season is TEXT ('2025-26'); cast to leading-year INT
--                   via SPLIT_PART(season, '-', 1)::INT
--   NHL           — no season column; derive from game_date
--                   (Sept-Dec = year, Jan-Aug = year - 1)
--   NHL           — close_puckline (not close_spread); total_goals (not
--                   total_points); no team-perspective spread flip because
--                   puckline is nearly always ±1.5
--   NHL           — no neutral_site; NCAAB has neutral tournament games
--                   but no flag; NFL SB/international untagged
--
-- Idempotent: DROP + CREATE at top of the ORIGINAL 20260901 migration
-- would be preferred but re-issuing the CREATE from this file replaces
-- the definition (materialized views can be re-created without dropping
-- if column shapes match; here we DROP + CREATE for safety since we're
-- adding rows from more sources).

DROP MATERIALIZED VIEW IF EXISTS public.team_recent_games;

CREATE MATERIALIZED VIEW public.team_recent_games AS
WITH all_games AS (

  -- ═══════════════════════════════════════════════════════════════════
  -- MLB (unchanged from 20260901)
  -- ═══════════════════════════════════════════════════════════════════
  SELECT
    'MLB'::TEXT AS sport,
    game_id, season, game_date,
    home_team AS team, away_team AS opp,
    TRUE AS is_home, FALSE AS is_neutral,
    home_score AS score_us, away_score AS score_them,
    home_win AS won,
    close_spread AS spread_line,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won'
      WHEN 'away_covered' THEN 'lost'
      WHEN 'push'         THEN 'push'
      ELSE NULL
    END AS spread_result,
    close_total AS total_line,
    LOWER(total_result) AS total_result,
    total_runs AS total_score
  FROM public.mlb_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team
  UNION ALL
  SELECT
    'MLB'::TEXT, game_id, season, game_date,
    away_team, home_team, FALSE, FALSE,
    away_score, home_score, (NOT home_win),
    -close_spread,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL
    END,
    close_total, LOWER(total_result), total_runs
  FROM public.mlb_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team

  -- ═══════════════════════════════════════════════════════════════════
  -- NCAAF (unchanged from 20260901)
  -- ═══════════════════════════════════════════════════════════════════
  UNION ALL
  SELECT
    'NCAAF'::TEXT, game_id, season, game_date,
    home_team, away_team, TRUE,
    COALESCE(neutral_site, FALSE),
    home_score, away_score, home_win,
    close_spread,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won' WHEN 'away_covered' THEN 'lost'
      WHEN 'push' THEN 'push' ELSE NULL
    END,
    close_total, LOWER(total_result), total_points
  FROM public.ncaaf_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team
  UNION ALL
  SELECT
    'NCAAF'::TEXT, game_id, season, game_date,
    away_team, home_team, FALSE,
    COALESCE(neutral_site, FALSE),
    away_score, home_score, (NOT home_win),
    -close_spread,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL
    END,
    close_total, LOWER(total_result), total_points
  FROM public.ncaaf_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team

  -- ═══════════════════════════════════════════════════════════════════
  -- NFL — season INT, no neutral_site
  -- ═══════════════════════════════════════════════════════════════════
  UNION ALL
  SELECT
    'NFL'::TEXT, game_id, season, game_date,
    home_team, away_team, TRUE, FALSE,
    home_score, away_score, home_win,
    close_spread,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won' WHEN 'away_covered' THEN 'lost'
      WHEN 'push' THEN 'push' ELSE NULL
    END,
    close_total, LOWER(total_result), total_points
  FROM public.nfl_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team
  UNION ALL
  SELECT
    'NFL'::TEXT, game_id, season, game_date,
    away_team, home_team, FALSE, FALSE,
    away_score, home_score, (NOT home_win),
    -close_spread,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL
    END,
    close_total, LOWER(total_result), total_points
  FROM public.nfl_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team

  -- ═══════════════════════════════════════════════════════════════════
  -- NBA — season TEXT '2025-26' → cast to leading-year INT
  -- ═══════════════════════════════════════════════════════════════════
  UNION ALL
  SELECT
    'NBA'::TEXT, game_id,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    game_date, home_team, away_team, TRUE, FALSE,
    home_score, away_score, home_win,
    close_spread,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won' WHEN 'away_covered' THEN 'lost'
      WHEN 'push' THEN 'push' ELSE NULL
    END,
    close_total, LOWER(total_result), total_points
  FROM public.nba_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team
  UNION ALL
  SELECT
    'NBA'::TEXT, game_id,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    game_date, away_team, home_team, FALSE, FALSE,
    away_score, home_score, (NOT home_win),
    -close_spread,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL
    END,
    close_total, LOWER(total_result), total_points
  FROM public.nba_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team

  -- ═══════════════════════════════════════════════════════════════════
  -- NCAAB — season TEXT '2025-26' → cast; no neutral_site flag
  -- ═══════════════════════════════════════════════════════════════════
  UNION ALL
  SELECT
    'NCAAB'::TEXT, game_id,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    game_date, home_team, away_team, TRUE, FALSE,
    home_score, away_score, home_win,
    close_spread,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won' WHEN 'away_covered' THEN 'lost'
      WHEN 'push' THEN 'push' ELSE NULL
    END,
    close_total, LOWER(total_result), total_points
  FROM public.ncaab_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team
  UNION ALL
  SELECT
    'NCAAB'::TEXT, game_id,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    game_date, away_team, home_team, FALSE, FALSE,
    away_score, home_score, (NOT home_win),
    -close_spread,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL
    END,
    close_total, LOWER(total_result), total_points
  FROM public.ncaab_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team

  -- ═══════════════════════════════════════════════════════════════════
  -- NHL — NO season column (derive from game_date); close_puckline (not
  -- close_spread); total_goals (not total_points). Puckline nearly always
  -- ±1.5 so team-perspective flip still works.
  -- ═══════════════════════════════════════════════════════════════════
  UNION ALL
  SELECT
    'NHL'::TEXT, game_id,
    CASE WHEN EXTRACT(MONTH FROM game_date) >= 9
         THEN EXTRACT(YEAR FROM game_date)::INT
         ELSE (EXTRACT(YEAR FROM game_date) - 1)::INT END,
    game_date, home_team, away_team, TRUE, FALSE,
    home_score, away_score, home_win,
    close_puckline AS spread_line,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won' WHEN 'away_covered' THEN 'lost'
      WHEN 'push' THEN 'push' ELSE NULL
    END,
    close_total, LOWER(total_result),
    total_goals AS total_score
  FROM public.nhl_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team
  UNION ALL
  SELECT
    'NHL'::TEXT, game_id,
    CASE WHEN EXTRACT(MONTH FROM game_date) >= 9
         THEN EXTRACT(YEAR FROM game_date)::INT
         ELSE (EXTRACT(YEAR FROM game_date) - 1)::INT END,
    game_date, away_team, home_team, FALSE, FALSE,
    away_score, home_score, (NOT home_win),
    -close_puckline,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost' WHEN 'away_covered' THEN 'won'
      WHEN 'push' THEN 'push' ELSE NULL
    END,
    close_total, LOWER(total_result), total_goals
  FROM public.nhl_game_results
  WHERE home_score IS NOT NULL AND home_team IS DISTINCT FROM away_team
)
SELECT
  sport, team, game_id, season, game_date, opp,
  is_home, is_neutral,
  score_us, score_them, won,
  spread_line, spread_result,
  total_line, total_result, total_score,
  ROW_NUMBER() OVER (PARTITION BY sport, team ORDER BY game_date DESC, game_id) AS seq
FROM all_games;

-- 2026-09-01: unique key includes is_home. Was (sport, team, game_id) but
-- NHL source has bad rows where both home_team and away_team are stored
-- as just "New York" (Rangers vs Islanders abbreviated ambiguously). The
-- WHERE home_team IS DISTINCT FROM away_team filter above skips those,
-- but keeping is_home in the key is defensive against any future dupe
-- surfacing from source-data quirks.
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_recent_games_uq
  ON public.team_recent_games (sport, team, game_id, is_home);
CREATE INDEX IF NOT EXISTS idx_team_recent_games_seq
  ON public.team_recent_games (sport, team, seq);
CREATE INDEX IF NOT EXISTS idx_team_recent_games_h2h
  ON public.team_recent_games (sport, team, opp, game_date DESC);

NOTIFY pgrst, 'reload schema';
