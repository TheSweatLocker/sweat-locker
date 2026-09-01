-- team_stats_rolling — clear the three deferred blocks (2026-09-01)
--
-- Adds MLB, NBA, NHL blocks to the universal per-team stat + rank rollup.
-- All three had schema-shape blockers documented in 20260901f header;
-- this migration resolves each:
--
--   MLB   — batting stats from mlb_team_offense + bullpen stats from
--           mlb_bullpen_stats. Pitching stats (ERA/WHIP/K per 9)
--           still gapped (pulled live per-cron in generate_mlb_game_reads
--           rather than persisted); follow-up ship needs a persisting
--           mlb_team_pitching puller — noted at bottom.
--
--   NBA   — team_abbrev vs full-name mismatch resolved via inline
--           abbrev_map CTE. Reads nba_game_results DISTINCT (team,
--           team_abbrev, season) pairs to bridge nba_team_stats.team_abbrev
--           into the full name used elsewhere. Matches the abbrev-first
--           pattern NBAFourFactorsCard already uses in prod.
--
--   NHL   — no source table exists; aggregates AVG per (team, season)
--           directly from nhl_game_context per-game columns. Season
--           derived from game_date (Sept-Dec = year, Jan-Aug = year - 1).
--           Skips l10_* rolling-window fields (not seasonal).
--
-- Idempotent DROP + CREATE. Refresh RPC unchanged
-- (refresh_team_stats_rolling — cross-sport, one shot).

DROP MATERIALIZED VIEW IF EXISTS public.team_stats_rolling;

CREATE MATERIALIZED VIEW public.team_stats_rolling AS
WITH

-- ═════════════════════════════════════════════════════════════════════
-- NBA abbrev bridge — HARDCODED 30-team mapping.
--
-- The DYNAMIC bridge attempted first (SELECT DISTINCT home_team,
-- home_abbrev FROM nba_game_results) failed post-migration spot-check:
--   1. nba_game_results.home_abbrev + away_abbrev are NULL on all 1,324
--      rows (schema exists, population never happened)
--   2. nba_team_stats.team_abbrev is TRUNCATED to 8 chars ('Boston C',
--      'Los Ange') — not real abbreviations. But CONFIRMED distinct
--      across all 30 teams so hardcoded mapping is safe.
--
-- Excludes 'Team Can/Chu/Ken/Sha' rows (2025 All-Star game format teams).
-- ═════════════════════════════════════════════════════════════════════
nba_abbrev_map AS (
  SELECT * FROM (VALUES
    ('Atlanta ',  'Atlanta Hawks'),
    ('Boston C',  'Boston Celtics'),
    ('Brooklyn',  'Brooklyn Nets'),
    ('Charlott',  'Charlotte Hornets'),
    ('Chicago ',  'Chicago Bulls'),
    ('Clevelan',  'Cleveland Cavaliers'),
    ('Dallas M',  'Dallas Mavericks'),
    ('Denver N',  'Denver Nuggets'),
    ('Detroit ',  'Detroit Pistons'),
    ('Golden S',  'Golden State Warriors'),
    ('Houston ',  'Houston Rockets'),
    ('Indiana ',  'Indiana Pacers'),
    ('LA Clipp',  'LA Clippers'),
    ('Los Ange',  'Los Angeles Lakers'),
    ('Memphis ',  'Memphis Grizzlies'),
    ('Miami He',  'Miami Heat'),
    ('Milwauke',  'Milwaukee Bucks'),
    ('Minnesot',  'Minnesota Timberwolves'),
    ('New Orle',  'New Orleans Pelicans'),
    ('New York',  'New York Knicks'),
    ('Oklahoma',  'Oklahoma City Thunder'),
    ('Orlando ',  'Orlando Magic'),
    ('Philadel',  'Philadelphia 76ers'),
    ('Phoenix ',  'Phoenix Suns'),
    ('Portland',  'Portland Trail Blazers'),
    ('Sacramen',  'Sacramento Kings'),
    ('San Anto',  'San Antonio Spurs'),
    ('Toronto ',  'Toronto Raptors'),
    ('Utah Jaz',  'Utah Jazz'),
    ('Washingt',  'Washington Wizards')
  ) AS t(abbrev, team)
),

-- ═════════════════════════════════════════════════════════════════════
-- NHL per-game unpivot — collapse home/away pairs into single rows keyed
-- on (team, season_int, metric). Then AVG-per-team-season below.
-- ═════════════════════════════════════════════════════════════════════
nhl_home_rows AS (
  SELECT
    home_team AS team,
    CASE WHEN EXTRACT(MONTH FROM game_date) >= 9
         THEN EXTRACT(YEAR FROM game_date)::INT
         ELSE (EXTRACT(YEAR FROM game_date) - 1)::INT END AS season,
    home_xgf_per60           AS xgf_per60,
    home_xga_per60           AS xga_per60,
    home_high_danger_for     AS hd_for,
    home_high_danger_against AS hd_against,
    home_pp_pct              AS pp_pct,
    home_pk_pct              AS pk_pct,
    home_5v5_cf              AS corsi_5v5
  FROM public.nhl_game_context
  WHERE home_team IS NOT NULL
),
nhl_away_rows AS (
  SELECT
    away_team AS team,
    CASE WHEN EXTRACT(MONTH FROM game_date) >= 9
         THEN EXTRACT(YEAR FROM game_date)::INT
         ELSE (EXTRACT(YEAR FROM game_date) - 1)::INT END AS season,
    away_xgf_per60,
    away_xga_per60,
    away_high_danger_for,
    away_high_danger_against,
    away_pp_pct,
    away_pk_pct,
    away_5v5_cf
  FROM public.nhl_game_context
  WHERE away_team IS NOT NULL
),
nhl_all AS (
  SELECT * FROM nhl_home_rows
  UNION ALL
  SELECT * FROM nhl_away_rows
),
nhl_season_avgs AS (
  SELECT
    team, season,
    AVG(xgf_per60)  AS xgf_per60_avg,
    AVG(xga_per60)  AS xga_per60_avg,
    AVG(hd_for)     AS hd_for_avg,
    AVG(hd_against) AS hd_against_avg,
    AVG(pp_pct)     AS pp_pct_avg,
    AVG(pk_pct)     AS pk_pct_avg,
    AVG(corsi_5v5)  AS corsi_5v5_avg,
    COUNT(*)        AS games_ctx
  FROM nhl_all
  GROUP BY team, season
  HAVING COUNT(*) >= 5  -- gate on 5+ games to avoid single-game noise
),

-- ═════════════════════════════════════════════════════════════════════
-- Base stats — one long-format row per (sport, team, season, stat_key)
-- with raw value + direction. Rank computed downstream.
-- ═════════════════════════════════════════════════════════════════════
base_stats AS (

  -- ─────────────────────────────────────────────────────────────────
  -- NCAAF (unchanged from 20260901f)
  -- ─────────────────────────────────────────────────────────────────
  SELECT 'NCAAF'::TEXT AS sport, team, season, 'pass_yds_pg'::TEXT AS stat_key,
    ROUND((pass_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1) AS raw_value,
    'higher'::TEXT AS direction, 'Pass Yds/G'::TEXT AS display_label, 'yd'::TEXT AS unit
  FROM public.ncaaf_team_stats WHERE pass_yards IS NOT NULL AND games > 0
  UNION ALL
  SELECT 'NCAAF', team, season, 'rush_yds_pg',
    ROUND((rush_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1),
    'higher', 'Rush Yds/G', 'yd'
  FROM public.ncaaf_team_stats WHERE rush_yards IS NOT NULL AND games > 0
  UNION ALL
  SELECT 'NCAAF', team, season, 'total_yds_pg',
    ROUND(((COALESCE(pass_yards, 0) + COALESCE(rush_yards, 0))::NUMERIC / NULLIF(games, 0))::NUMERIC, 1),
    'higher', 'Total Yds/G', 'yd'
  FROM public.ncaaf_team_stats WHERE (pass_yards IS NOT NULL OR rush_yards IS NOT NULL) AND games > 0
  UNION ALL
  SELECT 'NCAAF', team, season, 'third_down_pct',
    ROUND((third_down_conv::NUMERIC / NULLIF(third_downs, 0) * 100)::NUMERIC, 1),
    'higher', '3rd Down %', '%'
  FROM public.ncaaf_team_stats WHERE third_down_conv IS NOT NULL AND third_downs > 0
  UNION ALL
  SELECT 'NCAAF', team, season, 'turnovers_pg',
    ROUND((turnovers::NUMERIC / NULLIF(games, 0))::NUMERIC, 2),
    'lower', 'Turnovers/G', ''
  FROM public.ncaaf_team_stats WHERE turnovers IS NOT NULL AND games > 0
  UNION ALL
  SELECT 'NCAAF', team, season, 'penalty_yds_pg',
    ROUND((penalty_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1),
    'lower', 'Penalty Yds/G', 'yd'
  FROM public.ncaaf_team_stats WHERE penalty_yards IS NOT NULL AND games > 0
  UNION ALL
  SELECT 'NCAAF', team, season, 'off_epa_per_play',
    ROUND(off_epa_per_play::NUMERIC, 3), 'higher', 'Off EPA/Play', ''
  FROM public.ncaaf_team_stats WHERE off_epa_per_play IS NOT NULL
  UNION ALL
  SELECT 'NCAAF', team, season, 'off_success_rate',
    ROUND((off_success_rate * 100)::NUMERIC, 1), 'higher', 'Off Success %', '%'
  FROM public.ncaaf_team_stats WHERE off_success_rate IS NOT NULL
  UNION ALL
  SELECT 'NCAAF', team, season, 'off_explosiveness',
    ROUND(off_explosiveness::NUMERIC, 3), 'higher', 'Off Explosiveness', ''
  FROM public.ncaaf_team_stats WHERE off_explosiveness IS NOT NULL
  UNION ALL
  SELECT 'NCAAF', team, season, 'sp_overall',
    ROUND(sp_overall::NUMERIC, 2), 'higher', 'SP+', ''
  FROM public.ncaaf_team_stats WHERE sp_overall IS NOT NULL
  UNION ALL
  SELECT 'NCAAF', team, season, 'sp_offense',
    ROUND(sp_offense::NUMERIC, 2), 'higher', 'SP+ Offense', ''
  FROM public.ncaaf_team_stats WHERE sp_offense IS NOT NULL
  UNION ALL
  SELECT 'NCAAF', team, season, 'sp_defense',
    ROUND(sp_defense::NUMERIC, 2), 'lower', 'SP+ Defense', ''
  FROM public.ncaaf_team_stats WHERE sp_defense IS NOT NULL
  UNION ALL
  SELECT 'NCAAF', d.team, d.season, 'points_allowed_pg',
    ROUND(d.def_ppg::NUMERIC, 1), 'lower', 'Points Allowed/G', ''
  FROM public.ncaaf_team_defense_stats d
  WHERE d.def_ppg IS NOT NULL
    AND EXISTS (SELECT 1 FROM public.ncaaf_team_stats o WHERE o.team = d.team AND o.season = d.season)
  UNION ALL
  SELECT 'NCAAF', d.team, d.season, 'def_epa_per_play',
    ROUND(d.def_pass_epa_allowed::NUMERIC, 3), 'lower', 'Def Pass EPA', ''
  FROM public.ncaaf_team_defense_stats d
  WHERE d.def_pass_epa_allowed IS NOT NULL
    AND EXISTS (SELECT 1 FROM public.ncaaf_team_stats o WHERE o.team = d.team AND o.season = d.season)
  UNION ALL
  SELECT 'NCAAF', d.team, d.season, 'def_rush_epa_allowed',
    ROUND(d.def_rush_epa_allowed::NUMERIC, 3), 'lower', 'Def Rush EPA', ''
  FROM public.ncaaf_team_defense_stats d
  WHERE d.def_rush_epa_allowed IS NOT NULL
    AND EXISTS (SELECT 1 FROM public.ncaaf_team_stats o WHERE o.team = d.team AND o.season = d.season)
  UNION ALL
  SELECT 'NCAAF', d.team, d.season, 'def_success_rate_allowed',
    ROUND((d.def_success_rate_allowed * 100)::NUMERIC, 1), 'lower', 'Def Success % Allowed', '%'
  FROM public.ncaaf_team_defense_stats d
  WHERE d.def_success_rate_allowed IS NOT NULL
    AND EXISTS (SELECT 1 FROM public.ncaaf_team_stats o WHERE o.team = d.team AND o.season = d.season)

  -- ─────────────────────────────────────────────────────────────────
  -- NFL (unchanged from 20260901f)
  -- ─────────────────────────────────────────────────────────────────
  UNION ALL
  SELECT 'NFL', team, season, 'pass_yds_pg',
    ROUND((pass_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1), 'higher', 'Pass Yds/G', 'yd'
  FROM public.nfl_team_stats
  WHERE pass_yards IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'rush_yds_pg',
    ROUND((rush_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1), 'higher', 'Rush Yds/G', 'yd'
  FROM public.nfl_team_stats
  WHERE rush_yards IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'total_yds_pg',
    ROUND(((COALESCE(pass_yards, 0) + COALESCE(rush_yards, 0))::NUMERIC / NULLIF(games, 0))::NUMERIC, 1),
    'higher', 'Total Yds/G', 'yd'
  FROM public.nfl_team_stats
  WHERE (pass_yards IS NOT NULL OR rush_yards IS NOT NULL) AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'pass_tds_pg',
    ROUND((pass_tds::NUMERIC / NULLIF(games, 0))::NUMERIC, 2), 'higher', 'Pass TDs/G', ''
  FROM public.nfl_team_stats
  WHERE pass_tds IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'rush_tds_pg',
    ROUND((rush_tds::NUMERIC / NULLIF(games, 0))::NUMERIC, 2), 'higher', 'Rush TDs/G', ''
  FROM public.nfl_team_stats
  WHERE rush_tds IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'ints_pg',
    ROUND((pass_ints::NUMERIC / NULLIF(games, 0))::NUMERIC, 2), 'lower', 'INTs Thrown/G', ''
  FROM public.nfl_team_stats
  WHERE pass_ints IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'sacks_suffered_pg',
    ROUND((sacks_suffered::NUMERIC / NULLIF(games, 0))::NUMERIC, 2), 'lower', 'Sacks Allowed/G', ''
  FROM public.nfl_team_stats
  WHERE sacks_suffered IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'penalty_yds_pg',
    ROUND((penalty_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1), 'lower', 'Penalty Yds/G', 'yd'
  FROM public.nfl_team_stats
  WHERE penalty_yards IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'off_pass_epa',
    ROUND(pass_epa::NUMERIC, 3), 'higher', 'Off Pass EPA', ''
  FROM public.nfl_team_stats
  WHERE pass_epa IS NOT NULL AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'off_rush_epa',
    ROUND(rush_epa::NUMERIC, 3), 'higher', 'Off Rush EPA', ''
  FROM public.nfl_team_stats
  WHERE rush_epa IS NOT NULL AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'points_allowed_pg',
    ROUND(def_ppg::NUMERIC, 1), 'lower', 'Points Allowed/G', ''
  FROM public.nfl_team_defense_stats
  WHERE def_ppg IS NOT NULL AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'yds_allowed_pg',
    ROUND(def_ypg::NUMERIC, 1), 'lower', 'Yds Allowed/G', 'yd'
  FROM public.nfl_team_defense_stats
  WHERE def_ypg IS NOT NULL AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'pass_yds_allowed_pg',
    ROUND(def_pass_ypg::NUMERIC, 1), 'lower', 'Pass Yds Allowed/G', 'yd'
  FROM public.nfl_team_defense_stats
  WHERE def_pass_ypg IS NOT NULL AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'rush_yds_allowed_pg',
    ROUND(def_rush_ypg::NUMERIC, 1), 'lower', 'Rush Yds Allowed/G', 'yd'
  FROM public.nfl_team_defense_stats
  WHERE def_rush_ypg IS NOT NULL AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'def_pass_epa',
    ROUND(def_pass_epa_allowed::NUMERIC, 3), 'lower', 'Def Pass EPA', ''
  FROM public.nfl_team_defense_stats
  WHERE def_pass_epa_allowed IS NOT NULL AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'def_rush_epa',
    ROUND(def_rush_epa_allowed::NUMERIC, 3), 'lower', 'Def Rush EPA', ''
  FROM public.nfl_team_defense_stats
  WHERE def_rush_epa_allowed IS NOT NULL AND COALESCE(season_type, 'REG') = 'REG'

  -- ─────────────────────────────────────────────────────────────────
  -- NCAAB (unchanged from 20260901f)
  -- ─────────────────────────────────────────────────────────────────
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT, 'ppg_for',
    ROUND(ppg_for::NUMERIC, 1), 'higher', 'Points/G', ''
  FROM public.ncaab_team_efficiency WHERE ppg_for IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT, 'ppg_against',
    ROUND(ppg_against::NUMERIC, 1), 'lower', 'Points Allowed/G', ''
  FROM public.ncaab_team_efficiency WHERE ppg_against IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT, 'avg_margin',
    ROUND(avg_margin::NUMERIC, 2), 'higher', 'Avg Margin', ''
  FROM public.ncaab_team_efficiency WHERE avg_margin IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT, 'off_rating',
    ROUND(est_off_rating::NUMERIC, 2), 'higher', 'Off Rating', ''
  FROM public.ncaab_team_efficiency WHERE est_off_rating IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT, 'def_rating',
    ROUND(est_def_rating::NUMERIC, 2), 'lower', 'Def Rating', ''
  FROM public.ncaab_team_efficiency WHERE est_def_rating IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT, 'net_rating',
    ROUND(est_net_rating::NUMERIC, 2), 'higher', 'Net Rating', ''
  FROM public.ncaab_team_efficiency WHERE est_net_rating IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT, 'tempo',
    ROUND(est_tempo::NUMERIC, 1), 'higher', 'Tempo', ''
  FROM public.ncaab_team_efficiency WHERE est_tempo IS NOT NULL

  -- ═════════════════════════════════════════════════════════════════
  -- MLB — batting from mlb_team_offense + bullpen ERA/save%.
  -- Pitching stats (ERA/WHIP/K per 9) still gapped — pulled live in
  -- generate_mlb_game_reads.py not persisted; needs mlb_team_pitching
  -- puller in follow-up ship.
  -- ═════════════════════════════════════════════════════════════════
  UNION ALL
  SELECT 'MLB', team, season, 'team_avg',
    ROUND(avg::NUMERIC, 3), 'higher', 'AVG', ''
  FROM public.mlb_team_offense WHERE avg IS NOT NULL
  UNION ALL
  SELECT 'MLB', team, season, 'team_obp',
    ROUND(obp::NUMERIC, 3), 'higher', 'OBP', ''
  FROM public.mlb_team_offense WHERE obp IS NOT NULL
  UNION ALL
  SELECT 'MLB', team, season, 'team_slg',
    ROUND(slg::NUMERIC, 3), 'higher', 'SLG', ''
  FROM public.mlb_team_offense WHERE slg IS NOT NULL
  UNION ALL
  SELECT 'MLB', team, season, 'team_ops',
    ROUND(ops::NUMERIC, 3), 'higher', 'OPS', ''
  FROM public.mlb_team_offense WHERE ops IS NOT NULL
  UNION ALL
  SELECT 'MLB', team, season, 'team_woba',
    ROUND(woba::NUMERIC, 3), 'higher', 'wOBA', ''
  FROM public.mlb_team_offense WHERE woba IS NOT NULL
  UNION ALL
  SELECT 'MLB', team, season, 'team_wrc_plus',
    ROUND(wrc_plus::NUMERIC, 0), 'higher', 'wRC+', ''
  FROM public.mlb_team_offense WHERE wrc_plus IS NOT NULL
  UNION ALL
  SELECT 'MLB', team, season, 'team_iso',
    ROUND(iso::NUMERIC, 3), 'higher', 'ISO', ''
  FROM public.mlb_team_offense WHERE iso IS NOT NULL
  UNION ALL
  -- bb_pct + k_pct stored in percent form (e.g. 9.1 = 9.1%, not 0.091).
  -- Don't multiply — write raw.
  SELECT 'MLB', team, season, 'team_bb_pct',
    ROUND(bb_pct::NUMERIC, 1), 'higher', 'BB%', '%'
  FROM public.mlb_team_offense WHERE bb_pct IS NOT NULL
  UNION ALL
  SELECT 'MLB', team, season, 'team_k_pct',
    ROUND(k_pct::NUMERIC, 1), 'lower', 'K%', '%'
  FROM public.mlb_team_offense WHERE k_pct IS NOT NULL
  UNION ALL
  SELECT 'MLB', team, season, 'team_runs_pg',
    ROUND(runs_per_game::NUMERIC, 2), 'higher', 'Runs/G', ''
  FROM public.mlb_team_offense WHERE runs_per_game IS NOT NULL
  UNION ALL
  SELECT 'MLB', team, season, 'team_hr_pg',
    ROUND(hr_per_game::NUMERIC, 2), 'higher', 'HR/G', ''
  FROM public.mlb_team_offense WHERE hr_per_game IS NOT NULL
  -- mlb_bullpen_stats.season is TEXT ('2026') while mlb_team_offense.season
  -- is INT — cast to INT for UNION compatibility.
  UNION ALL
  SELECT 'MLB', team, NULLIF(season, '')::INT, 'bullpen_era',
    ROUND(bullpen_era::NUMERIC, 2), 'lower', 'Bullpen ERA', ''
  FROM public.mlb_bullpen_stats WHERE bullpen_era IS NOT NULL AND season ~ '^[0-9]+$'
  UNION ALL
  -- save_pct stored in percent form (e.g. 67.2 = 67.2%). Write raw.
  SELECT 'MLB', team, NULLIF(season, '')::INT, 'bullpen_save_pct',
    ROUND(save_pct::NUMERIC, 1), 'higher', 'Bullpen Save %', '%'
  FROM public.mlb_bullpen_stats WHERE save_pct IS NOT NULL AND season ~ '^[0-9]+$'

  -- ═════════════════════════════════════════════════════════════════
  -- NBA — bridge team_abbrev to full team name via nba_abbrev_map CTE.
  -- Season 'YYYY-YY' → leading-year INT.
  -- ═════════════════════════════════════════════════════════════════
  UNION ALL
  SELECT 'NBA', m.team,
    NULLIF(SPLIT_PART(s.season, '-', 1), '')::INT, 'off_rating',
    ROUND(s.off_rating::NUMERIC, 2), 'higher', 'Off Rating', ''
  FROM public.nba_team_stats s
  JOIN nba_abbrev_map m ON m.abbrev = s.team_abbrev
  WHERE s.off_rating IS NOT NULL
  UNION ALL
  SELECT 'NBA', m.team,
    NULLIF(SPLIT_PART(s.season, '-', 1), '')::INT, 'def_rating',
    ROUND(s.def_rating::NUMERIC, 2), 'lower', 'Def Rating', ''
  FROM public.nba_team_stats s
  JOIN nba_abbrev_map m ON m.abbrev = s.team_abbrev
  WHERE s.def_rating IS NOT NULL
  UNION ALL
  SELECT 'NBA', m.team,
    NULLIF(SPLIT_PART(s.season, '-', 1), '')::INT, 'net_rating',
    ROUND(s.net_rating::NUMERIC, 2), 'higher', 'Net Rating', ''
  FROM public.nba_team_stats s
  JOIN nba_abbrev_map m ON m.abbrev = s.team_abbrev
  WHERE s.net_rating IS NOT NULL
  UNION ALL
  SELECT 'NBA', m.team,
    NULLIF(SPLIT_PART(s.season, '-', 1), '')::INT, 'pace',
    ROUND(s.pace::NUMERIC, 1), 'higher', 'Pace', ''
  FROM public.nba_team_stats s
  JOIN nba_abbrev_map m ON m.abbrev = s.team_abbrev
  WHERE s.pace IS NOT NULL
  UNION ALL
  SELECT 'NBA', m.team,
    NULLIF(SPLIT_PART(s.season, '-', 1), '')::INT, 'efg_pct',
    ROUND((s.efg_pct * 100)::NUMERIC, 1), 'higher', 'eFG %', '%'
  FROM public.nba_team_stats s
  JOIN nba_abbrev_map m ON m.abbrev = s.team_abbrev
  WHERE s.efg_pct IS NOT NULL
  UNION ALL
  SELECT 'NBA', m.team,
    NULLIF(SPLIT_PART(s.season, '-', 1), '')::INT, 'tov_pct',
    ROUND((s.tov_pct * 100)::NUMERIC, 1), 'lower', 'TOV %', '%'
  FROM public.nba_team_stats s
  JOIN nba_abbrev_map m ON m.abbrev = s.team_abbrev
  WHERE s.tov_pct IS NOT NULL
  UNION ALL
  SELECT 'NBA', m.team,
    NULLIF(SPLIT_PART(s.season, '-', 1), '')::INT, 'orb_pct',
    ROUND((s.orb_pct * 100)::NUMERIC, 1), 'higher', 'ORB %', '%'
  FROM public.nba_team_stats s
  JOIN nba_abbrev_map m ON m.abbrev = s.team_abbrev
  WHERE s.orb_pct IS NOT NULL
  UNION ALL
  SELECT 'NBA', m.team,
    NULLIF(SPLIT_PART(s.season, '-', 1), '')::INT, 'ft_rate',
    ROUND(s.ft_rate::NUMERIC, 3), 'higher', 'FT Rate', ''
  FROM public.nba_team_stats s
  JOIN nba_abbrev_map m ON m.abbrev = s.team_abbrev
  WHERE s.ft_rate IS NOT NULL
  UNION ALL
  SELECT 'NBA', m.team,
    NULLIF(SPLIT_PART(s.season, '-', 1), '')::INT, 'opp_efg_pct',
    ROUND((s.opp_efg_pct * 100)::NUMERIC, 1), 'lower', 'Opp eFG %', '%'
  FROM public.nba_team_stats s
  JOIN nba_abbrev_map m ON m.abbrev = s.team_abbrev
  WHERE s.opp_efg_pct IS NOT NULL
  UNION ALL
  SELECT 'NBA', m.team,
    NULLIF(SPLIT_PART(s.season, '-', 1), '')::INT, 'opp_tov_pct',
    ROUND((s.opp_tov_pct * 100)::NUMERIC, 1), 'higher', 'Opp TOV %', '%'
  FROM public.nba_team_stats s
  JOIN nba_abbrev_map m ON m.abbrev = s.team_abbrev
  WHERE s.opp_tov_pct IS NOT NULL

  -- ═════════════════════════════════════════════════════════════════
  -- NHL — aggregated from nhl_game_context per-game columns (no
  -- season table exists). AVG over all played games per (team, season),
  -- gated at 5+ games via HAVING in nhl_season_avgs CTE.
  -- ═════════════════════════════════════════════════════════════════
  UNION ALL
  SELECT 'NHL', team, season, 'xgf_per60',
    ROUND(xgf_per60_avg::NUMERIC, 2), 'higher', 'xGF/60', ''
  FROM nhl_season_avgs WHERE xgf_per60_avg IS NOT NULL
  UNION ALL
  SELECT 'NHL', team, season, 'xga_per60',
    ROUND(xga_per60_avg::NUMERIC, 2), 'lower', 'xGA/60', ''
  FROM nhl_season_avgs WHERE xga_per60_avg IS NOT NULL
  UNION ALL
  SELECT 'NHL', team, season, 'high_danger_for',
    ROUND(hd_for_avg::NUMERIC, 2), 'higher', 'HD Chances For', ''
  FROM nhl_season_avgs WHERE hd_for_avg IS NOT NULL
  UNION ALL
  SELECT 'NHL', team, season, 'high_danger_against',
    ROUND(hd_against_avg::NUMERIC, 2), 'lower', 'HD Chances Against', ''
  FROM nhl_season_avgs WHERE hd_against_avg IS NOT NULL
  UNION ALL
  SELECT 'NHL', team, season, 'pp_pct',
    ROUND((pp_pct_avg * 100)::NUMERIC, 1), 'higher', 'PP %', '%'
  FROM nhl_season_avgs WHERE pp_pct_avg IS NOT NULL
  UNION ALL
  SELECT 'NHL', team, season, 'pk_pct',
    ROUND((pk_pct_avg * 100)::NUMERIC, 1), 'higher', 'PK %', '%'
  FROM nhl_season_avgs WHERE pk_pct_avg IS NOT NULL
  UNION ALL
  SELECT 'NHL', team, season, 'corsi_5v5',
    ROUND((corsi_5v5_avg * 100)::NUMERIC, 1), 'higher', '5v5 CF %', '%'
  FROM nhl_season_avgs WHERE corsi_5v5_avg IS NOT NULL
),
ranked AS (
  SELECT
    sport, team, season, stat_key, raw_value, direction, display_label, unit,
    CASE direction
      WHEN 'higher' THEN RANK() OVER (
        PARTITION BY sport, season, stat_key
        ORDER BY raw_value DESC NULLS LAST)
      WHEN 'lower' THEN RANK() OVER (
        PARTITION BY sport, season, stat_key
        ORDER BY raw_value ASC NULLS LAST)
    END AS rank,
    COUNT(*) OVER (PARTITION BY sport, season, stat_key) AS league_size
  FROM base_stats
  WHERE raw_value IS NOT NULL AND season IS NOT NULL
)
SELECT
  sport, team, season, stat_key,
  raw_value, rank, league_size, direction,
  display_label, unit,
  NOW() AS refreshed_at
FROM ranked;

CREATE UNIQUE INDEX IF NOT EXISTS idx_team_stats_rolling_uq
  ON public.team_stats_rolling (sport, team, season, stat_key);
CREATE INDEX IF NOT EXISTS idx_team_stats_rolling_team
  ON public.team_stats_rolling (sport, team, season);

NOTIFY pgrst, 'reload schema';
