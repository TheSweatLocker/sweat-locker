-- Backfill home_abbrev + away_abbrev on nba_game_results (2026-09-01)
--
-- Per 9/1 audit: all 1,324 rows had home_abbrev / away_abbrev = NULL
-- (schema exists, was never populated by the historical backfill script
-- at _legacy/backfill_nba_history.py). nba_resolve_results.py DOES
-- populate these going forward, but historical rows sat empty — meaning
-- team_recent_games matview couldn't render abbrev fallbacks for older
-- games and any future consumer joining on home_abbrev would fail.
--
-- Fix: single CASE-based UPDATE mapping full-name → 3-letter ESPN abbrev.
-- Same 30-team map as nba_elo.py / nba_four_factors_pull.py.
--
-- Runs on rows where the field is NULL (idempotent — re-running affects
-- nothing since already-set values are excluded).

UPDATE public.nba_game_results
SET home_abbrev = CASE home_team
  WHEN 'Atlanta Hawks'            THEN 'ATL'
  WHEN 'Boston Celtics'           THEN 'BOS'
  WHEN 'Brooklyn Nets'            THEN 'BKN'
  WHEN 'Charlotte Hornets'        THEN 'CHA'
  WHEN 'Chicago Bulls'            THEN 'CHI'
  WHEN 'Cleveland Cavaliers'      THEN 'CLE'
  WHEN 'Dallas Mavericks'         THEN 'DAL'
  WHEN 'Denver Nuggets'           THEN 'DEN'
  WHEN 'Detroit Pistons'          THEN 'DET'
  WHEN 'Golden State Warriors'    THEN 'GSW'
  WHEN 'Houston Rockets'          THEN 'HOU'
  WHEN 'Indiana Pacers'           THEN 'IND'
  WHEN 'LA Clippers'              THEN 'LAC'
  WHEN 'Los Angeles Clippers'     THEN 'LAC'  -- bref/legacy variant
  WHEN 'Los Angeles Lakers'       THEN 'LAL'
  WHEN 'Memphis Grizzlies'        THEN 'MEM'
  WHEN 'Miami Heat'               THEN 'MIA'
  WHEN 'Milwaukee Bucks'          THEN 'MIL'
  WHEN 'Minnesota Timberwolves'   THEN 'MIN'
  WHEN 'New Orleans Pelicans'     THEN 'NOP'
  WHEN 'New York Knicks'          THEN 'NYK'
  WHEN 'Oklahoma City Thunder'    THEN 'OKC'
  WHEN 'Orlando Magic'            THEN 'ORL'
  WHEN 'Philadelphia 76ers'       THEN 'PHI'
  WHEN 'Phoenix Suns'             THEN 'PHX'
  WHEN 'Portland Trail Blazers'   THEN 'POR'
  WHEN 'Sacramento Kings'         THEN 'SAC'
  WHEN 'San Antonio Spurs'        THEN 'SAS'
  WHEN 'Toronto Raptors'          THEN 'TOR'
  WHEN 'Utah Jazz'                THEN 'UTA'
  WHEN 'Washington Wizards'       THEN 'WAS'
  ELSE home_abbrev
END
WHERE home_abbrev IS NULL AND home_team IS NOT NULL;

UPDATE public.nba_game_results
SET away_abbrev = CASE away_team
  WHEN 'Atlanta Hawks'            THEN 'ATL'
  WHEN 'Boston Celtics'           THEN 'BOS'
  WHEN 'Brooklyn Nets'            THEN 'BKN'
  WHEN 'Charlotte Hornets'        THEN 'CHA'
  WHEN 'Chicago Bulls'            THEN 'CHI'
  WHEN 'Cleveland Cavaliers'      THEN 'CLE'
  WHEN 'Dallas Mavericks'         THEN 'DAL'
  WHEN 'Denver Nuggets'           THEN 'DEN'
  WHEN 'Detroit Pistons'          THEN 'DET'
  WHEN 'Golden State Warriors'    THEN 'GSW'
  WHEN 'Houston Rockets'          THEN 'HOU'
  WHEN 'Indiana Pacers'           THEN 'IND'
  WHEN 'LA Clippers'              THEN 'LAC'
  WHEN 'Los Angeles Clippers'     THEN 'LAC'
  WHEN 'Los Angeles Lakers'       THEN 'LAL'
  WHEN 'Memphis Grizzlies'        THEN 'MEM'
  WHEN 'Miami Heat'               THEN 'MIA'
  WHEN 'Milwaukee Bucks'          THEN 'MIL'
  WHEN 'Minnesota Timberwolves'   THEN 'MIN'
  WHEN 'New Orleans Pelicans'     THEN 'NOP'
  WHEN 'New York Knicks'          THEN 'NYK'
  WHEN 'Oklahoma City Thunder'    THEN 'OKC'
  WHEN 'Orlando Magic'            THEN 'ORL'
  WHEN 'Philadelphia 76ers'       THEN 'PHI'
  WHEN 'Phoenix Suns'             THEN 'PHX'
  WHEN 'Portland Trail Blazers'   THEN 'POR'
  WHEN 'Sacramento Kings'         THEN 'SAC'
  WHEN 'San Antonio Spurs'        THEN 'SAS'
  WHEN 'Toronto Raptors'          THEN 'TOR'
  WHEN 'Utah Jazz'                THEN 'UTA'
  WHEN 'Washington Wizards'       THEN 'WAS'
  ELSE away_abbrev
END
WHERE away_abbrev IS NULL AND away_team IS NOT NULL;

NOTIFY pgrst, 'reload schema';
