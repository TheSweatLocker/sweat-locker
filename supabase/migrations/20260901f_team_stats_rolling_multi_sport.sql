-- team_stats_rolling — extend to NFL + NCAAB (2026-09-01)
--
-- Adds NFL and NCAAB blocks to the universal per-team stat + rank rollup.
-- MLB / NBA / NHL deferred for source-schema reasons:
--
--   MLB   — team-level stats are per-9-innings or per-PA (ERA, OPS,
--           WHIP), not per-game averages. Semantically different from
--           football/basketball; needs its own stat-key set + DIRECTION
--           conventions. Follow-up ship.
--   NBA   — nba_team_stats keys on `team_abbrev` while nba_game_results
--           and app props use full team names ('Los Angeles Lakers').
--           Needs a name-mapping table or column alignment first.
--   NHL   — no nhl_team_stats table exists. Season-level analytics live
--           per-game inside nhl_game_context. Needs an aggregator to
--           extract per-team season averages before it can rank.
--
-- Idempotent: DROP + CREATE, replaces existing definition.

DROP MATERIALIZED VIEW IF EXISTS public.team_stats_rolling;

CREATE MATERIALIZED VIEW public.team_stats_rolling AS
WITH base_stats AS (

  -- ═══════════════════════════════════════════════════════════════════
  -- NCAAF · offense (per-game volumes + rates)
  -- ═══════════════════════════════════════════════════════════════════
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
    ROUND(off_epa_per_play::NUMERIC, 3),
    'higher', 'Off EPA/Play', ''
  FROM public.ncaaf_team_stats WHERE off_epa_per_play IS NOT NULL
  UNION ALL
  SELECT 'NCAAF', team, season, 'off_success_rate',
    ROUND((off_success_rate * 100)::NUMERIC, 1),
    'higher', 'Off Success %', '%'
  FROM public.ncaaf_team_stats WHERE off_success_rate IS NOT NULL
  UNION ALL
  SELECT 'NCAAF', team, season, 'off_explosiveness',
    ROUND(off_explosiveness::NUMERIC, 3),
    'higher', 'Off Explosiveness', ''
  FROM public.ncaaf_team_stats WHERE off_explosiveness IS NOT NULL
  UNION ALL
  SELECT 'NCAAF', team, season, 'sp_overall',
    ROUND(sp_overall::NUMERIC, 2),
    'higher', 'SP+', ''
  FROM public.ncaaf_team_stats WHERE sp_overall IS NOT NULL
  UNION ALL
  SELECT 'NCAAF', team, season, 'sp_offense',
    ROUND(sp_offense::NUMERIC, 2),
    'higher', 'SP+ Offense', ''
  FROM public.ncaaf_team_stats WHERE sp_offense IS NOT NULL
  UNION ALL
  SELECT 'NCAAF', team, season, 'sp_defense',
    ROUND(sp_defense::NUMERIC, 2),
    'lower', 'SP+ Defense', ''
  FROM public.ncaaf_team_stats WHERE sp_defense IS NOT NULL

  -- NCAAF defense (FBS-only via EXISTS filter)
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

  -- ═══════════════════════════════════════════════════════════════════
  -- NFL · offense (per-game volumes + advanced)
  -- Source: nfl_team_stats (season_type='REG'; playoff excluded from ranks)
  -- ═══════════════════════════════════════════════════════════════════
  UNION ALL
  SELECT 'NFL', team, season, 'pass_yds_pg',
    ROUND((pass_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1),
    'higher', 'Pass Yds/G', 'yd'
  FROM public.nfl_team_stats
  WHERE pass_yards IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'rush_yds_pg',
    ROUND((rush_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1),
    'higher', 'Rush Yds/G', 'yd'
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
    ROUND((pass_tds::NUMERIC / NULLIF(games, 0))::NUMERIC, 2),
    'higher', 'Pass TDs/G', ''
  FROM public.nfl_team_stats
  WHERE pass_tds IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'rush_tds_pg',
    ROUND((rush_tds::NUMERIC / NULLIF(games, 0))::NUMERIC, 2),
    'higher', 'Rush TDs/G', ''
  FROM public.nfl_team_stats
  WHERE rush_tds IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'ints_pg',
    ROUND((pass_ints::NUMERIC / NULLIF(games, 0))::NUMERIC, 2),
    'lower', 'INTs Thrown/G', ''
  FROM public.nfl_team_stats
  WHERE pass_ints IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'sacks_suffered_pg',
    ROUND((sacks_suffered::NUMERIC / NULLIF(games, 0))::NUMERIC, 2),
    'lower', 'Sacks Allowed/G', ''
  FROM public.nfl_team_stats
  WHERE sacks_suffered IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'penalty_yds_pg',
    ROUND((penalty_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1),
    'lower', 'Penalty Yds/G', 'yd'
  FROM public.nfl_team_stats
  WHERE penalty_yards IS NOT NULL AND games > 0 AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'off_pass_epa',
    ROUND(pass_epa::NUMERIC, 3),
    'higher', 'Off Pass EPA', ''
  FROM public.nfl_team_stats
  WHERE pass_epa IS NOT NULL AND COALESCE(season_type, 'REG') = 'REG'
  UNION ALL
  SELECT 'NFL', team, season, 'off_rush_epa',
    ROUND(rush_epa::NUMERIC, 3),
    'higher', 'Off Rush EPA', ''
  FROM public.nfl_team_stats
  WHERE rush_epa IS NOT NULL AND COALESCE(season_type, 'REG') = 'REG'

  -- NFL defense (already per-game in nfl_team_defense_stats)
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

  -- ═══════════════════════════════════════════════════════════════════
  -- NCAAB · efficiency panel (multi-source per feedback_no_kenpom_attribution
  -- naming rule — labels say "Efficiency", never "KenPom"). Season TEXT
  -- '2025-26' cast to leading-year INT.
  -- ═══════════════════════════════════════════════════════════════════
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    'ppg_for',
    ROUND(ppg_for::NUMERIC, 1), 'higher', 'Points/G', ''
  FROM public.ncaab_team_efficiency
  WHERE ppg_for IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    'ppg_against',
    ROUND(ppg_against::NUMERIC, 1), 'lower', 'Points Allowed/G', ''
  FROM public.ncaab_team_efficiency
  WHERE ppg_against IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    'avg_margin',
    ROUND(avg_margin::NUMERIC, 2), 'higher', 'Avg Margin', ''
  FROM public.ncaab_team_efficiency
  WHERE avg_margin IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    'off_rating',
    ROUND(est_off_rating::NUMERIC, 2), 'higher', 'Off Rating', ''
  FROM public.ncaab_team_efficiency
  WHERE est_off_rating IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    'def_rating',
    ROUND(est_def_rating::NUMERIC, 2), 'lower', 'Def Rating', ''
  FROM public.ncaab_team_efficiency
  WHERE est_def_rating IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    'net_rating',
    ROUND(est_net_rating::NUMERIC, 2), 'higher', 'Net Rating', ''
  FROM public.ncaab_team_efficiency
  WHERE est_net_rating IS NOT NULL
  UNION ALL
  SELECT 'NCAAB', team,
    NULLIF(SPLIT_PART(season, '-', 1), '')::INT,
    'tempo',
    ROUND(est_tempo::NUMERIC, 1), 'higher', 'Tempo', ''
  FROM public.ncaab_team_efficiency
  WHERE est_tempo IS NOT NULL

  -- FUTURE: MLB / NBA / NHL blocks. See header comment for schema constraints.
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
