-- team_stats_rolling matview (2026-09-01)
--
-- Tier 1 #3 rollup per project_rolling_rollup_architecture_901.
-- Universal LONG-FORMAT per-team stat with raw value + league rank.
-- Kills these anti-patterns identified in the 9/1 audit:
--
--   1. home_team_stats_summary / away_team_stats_summary JSONB blobs
--      on nfl_game_context + ncaaf_game_context — non-queryable,
--      duplicated per-game, hard-coded keys, no cross-team ranking
--   2. NCAAFTeamMatchupCard client-side per-game math (line 1325-1337)
--      and fuzzy-substring team matching (line 1284-1294) to reconcile
--      Odds-API mascot names vs CFBD short names
--
-- With this matview, GameDetailV2 can just read
--   WHERE sport=? AND team IN (home, away) AND season=?
-- and get everything it needs, ranks included.
--
-- Rows per (sport, team, season, stat_key):
--   direction: 'higher' | 'lower'  (which end of the ranking is "best")
--   raw_value: the per-game average (or ratio, or rating)
--   rank:      1..league_size, best rank = 1 regardless of direction
--   league_size: total teams ranked in this (sport, season, stat_key) bucket
--
-- Coverage this migration: NCAAF (user's primary Tier 1 test sport +
-- where the client-side compute anti-pattern lives). MLB and other sports
-- queued as follow-up ships when their team-stats sources are audited.
--
-- Refresh: refresh_team_stats_rolling() RPC (cross-sport, one shot).

DROP MATERIALIZED VIEW IF EXISTS public.team_stats_rolling;

CREATE MATERIALIZED VIEW public.team_stats_rolling AS
WITH base_stats AS (

  -- ═══════════════════════════════════════════════════════════════════
  -- NCAAF · offense per-game volumes + rates from ncaaf_team_stats
  -- ═══════════════════════════════════════════════════════════════════
  -- Passing yards per game
  SELECT 'NCAAF'::TEXT AS sport, team, season, 'pass_yds_pg'::TEXT AS stat_key,
    ROUND((pass_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1) AS raw_value,
    'higher'::TEXT AS direction, 'Pass Yds/G'::TEXT AS display_label, 'yd'::TEXT AS unit
  FROM public.ncaaf_team_stats
  WHERE pass_yards IS NOT NULL AND games > 0

  UNION ALL
  SELECT 'NCAAF', team, season, 'rush_yds_pg',
    ROUND((rush_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1),
    'higher', 'Rush Yds/G', 'yd'
  FROM public.ncaaf_team_stats
  WHERE rush_yards IS NOT NULL AND games > 0

  UNION ALL
  SELECT 'NCAAF', team, season, 'total_yds_pg',
    ROUND(((COALESCE(pass_yards, 0) + COALESCE(rush_yards, 0))::NUMERIC / NULLIF(games, 0))::NUMERIC, 1),
    'higher', 'Total Yds/G', 'yd'
  FROM public.ncaaf_team_stats
  WHERE (pass_yards IS NOT NULL OR rush_yards IS NOT NULL) AND games > 0

  UNION ALL
  SELECT 'NCAAF', team, season, 'third_down_pct',
    ROUND((third_down_conv::NUMERIC / NULLIF(third_downs, 0) * 100)::NUMERIC, 1),
    'higher', '3rd Down %', '%'
  FROM public.ncaaf_team_stats
  WHERE third_down_conv IS NOT NULL AND third_downs > 0

  UNION ALL
  SELECT 'NCAAF', team, season, 'turnovers_pg',
    ROUND((turnovers::NUMERIC / NULLIF(games, 0))::NUMERIC, 2),
    'lower', 'Turnovers/G', ''
  FROM public.ncaaf_team_stats
  WHERE turnovers IS NOT NULL AND games > 0

  UNION ALL
  SELECT 'NCAAF', team, season, 'penalty_yds_pg',
    ROUND((penalty_yards::NUMERIC / NULLIF(games, 0))::NUMERIC, 1),
    'lower', 'Penalty Yds/G', 'yd'
  FROM public.ncaaf_team_stats
  WHERE penalty_yards IS NOT NULL AND games > 0

  UNION ALL
  -- Advanced offense metrics (EPA / success / explosiveness)
  SELECT 'NCAAF', team, season, 'off_epa_per_play',
    ROUND(off_epa_per_play::NUMERIC, 3),
    'higher', 'Off EPA/Play', ''
  FROM public.ncaaf_team_stats
  WHERE off_epa_per_play IS NOT NULL

  UNION ALL
  SELECT 'NCAAF', team, season, 'off_success_rate',
    ROUND((off_success_rate * 100)::NUMERIC, 1),
    'higher', 'Off Success %', '%'
  FROM public.ncaaf_team_stats
  WHERE off_success_rate IS NOT NULL

  UNION ALL
  SELECT 'NCAAF', team, season, 'off_explosiveness',
    ROUND(off_explosiveness::NUMERIC, 3),
    'higher', 'Off Explosiveness', ''
  FROM public.ncaaf_team_stats
  WHERE off_explosiveness IS NOT NULL

  UNION ALL
  -- SP+ ratings (composite efficiency model)
  SELECT 'NCAAF', team, season, 'sp_overall',
    ROUND(sp_overall::NUMERIC, 2),
    'higher', 'SP+', ''
  FROM public.ncaaf_team_stats
  WHERE sp_overall IS NOT NULL

  UNION ALL
  SELECT 'NCAAF', team, season, 'sp_offense',
    ROUND(sp_offense::NUMERIC, 2),
    'higher', 'SP+ Offense', ''
  FROM public.ncaaf_team_stats
  WHERE sp_offense IS NOT NULL

  UNION ALL
  SELECT 'NCAAF', team, season, 'sp_defense',
    ROUND(sp_defense::NUMERIC, 2),
    'lower', 'SP+ Defense', ''  -- SP+ defense: LOWER = better (opponent-adjusted points allowed)
  FROM public.ncaaf_team_stats
  WHERE sp_defense IS NOT NULL

  UNION ALL
  -- ═══════════════════════════════════════════════════════════════════
  -- NCAAF · defense per-game (opponent-attribution) from ncaaf_team_defense_stats
  --
  -- 🎯 FBS-ONLY FILTER (2026-09-01 v2): ncaaf_team_defense_stats holds
  -- ~700 rows per season including FCS/D2/JUCO opponents that showed up
  -- on FBS schedules. Ranking Alabama "615/699" mixed in those non-FBS
  -- teams and made the rank useless. Filter via EXISTS on ncaaf_team_stats
  -- (which is FBS-only from CFBD's /stats/season/advanced endpoint) so
  -- the league_size stays consistent (~136) with the offense side.
  -- ═══════════════════════════════════════════════════════════════════
  SELECT 'NCAAF', d.team, d.season, 'points_allowed_pg',
    ROUND(d.def_ppg::NUMERIC, 1),
    'lower', 'Points Allowed/G', ''
  FROM public.ncaaf_team_defense_stats d
  WHERE d.def_ppg IS NOT NULL
    AND EXISTS (SELECT 1 FROM public.ncaaf_team_stats o
                WHERE o.team = d.team AND o.season = d.season)

  UNION ALL
  SELECT 'NCAAF', d.team, d.season, 'def_epa_per_play',
    ROUND(d.def_pass_epa_allowed::NUMERIC, 3),
    'lower', 'Def Pass EPA', ''
  FROM public.ncaaf_team_defense_stats d
  WHERE d.def_pass_epa_allowed IS NOT NULL
    AND EXISTS (SELECT 1 FROM public.ncaaf_team_stats o
                WHERE o.team = d.team AND o.season = d.season)

  UNION ALL
  SELECT 'NCAAF', d.team, d.season, 'def_rush_epa_allowed',
    ROUND(d.def_rush_epa_allowed::NUMERIC, 3),
    'lower', 'Def Rush EPA', ''
  FROM public.ncaaf_team_defense_stats d
  WHERE d.def_rush_epa_allowed IS NOT NULL
    AND EXISTS (SELECT 1 FROM public.ncaaf_team_stats o
                WHERE o.team = d.team AND o.season = d.season)

  UNION ALL
  SELECT 'NCAAF', d.team, d.season, 'def_success_rate_allowed',
    ROUND((d.def_success_rate_allowed * 100)::NUMERIC, 1),
    'lower', 'Def Success % Allowed', '%'
  FROM public.ncaaf_team_defense_stats d
  WHERE d.def_success_rate_allowed IS NOT NULL
    AND EXISTS (SELECT 1 FROM public.ncaaf_team_stats o
                WHERE o.team = d.team AND o.season = d.season)

  -- FUTURE: MLB / NFL / NBA / NCAAB / NHL blocks land here as each sport's
  -- team-stats sources are audited and per-game averages computable.
),
ranked AS (
  SELECT
    sport, team, season, stat_key, raw_value, direction, display_label, unit,
    CASE direction
      WHEN 'higher' THEN RANK() OVER (
        PARTITION BY sport, season, stat_key
        ORDER BY raw_value DESC NULLS LAST
      )
      WHEN 'lower'  THEN RANK() OVER (
        PARTITION BY sport, season, stat_key
        ORDER BY raw_value ASC NULLS LAST
      )
    END AS rank,
    COUNT(*) OVER (PARTITION BY sport, season, stat_key) AS league_size
  FROM base_stats
  WHERE raw_value IS NOT NULL
)
SELECT
  sport, team, season, stat_key,
  raw_value, rank, league_size, direction,
  display_label, unit,
  NOW() AS refreshed_at
FROM ranked;


-- Unique index for REFRESH CONCURRENTLY + primary lookup
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_stats_rolling_uq
  ON public.team_stats_rolling (sport, team, season, stat_key);

CREATE INDEX IF NOT EXISTS idx_team_stats_rolling_team
  ON public.team_stats_rolling (sport, team, season);


-- ═══════════════════════════════════════════════════════════════════════
-- Refresh helper — call from each sport's pipeline after team stats
-- are updated. Cross-sport refresh in one shot (small view footprint —
-- ~130 teams × 16 stats × 2 seasons = ~4k rows for NCAAF only).
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.refresh_team_stats_rolling()
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY public.team_stats_rolling;
END $$;

NOTIFY pgrst, 'reload schema';
