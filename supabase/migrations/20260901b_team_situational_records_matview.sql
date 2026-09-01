-- team_situational_records matview (2026-09-01)
--
-- Tier 1 #2 rollup per project_rolling_rollup_architecture_901.
-- Universal LONG-FORMAT per-team W-L-P record broken out by
-- (market × filter). Powers the Situational Results card on game
-- detail with the sub-tab pattern the user directed (Spread / Total /
-- Moneyline × Overall / L10 / L5 / Home / Away / Fav / Dog).
--
-- Long format design (vs the existing wide-format {sport}_team_home_road_tendencies
-- matviews) means:
--   - App reads one query and pivots in JS: WHERE sport=? AND team IN (h,a)
--   - New filter = one INSERT in the aggregator, no schema change
--   - New market = one INSERT, no schema change
--   - Rows-per-team bounded: 3 markets × 7 filters = 21 max (small)
--
-- The existing wide matviews stay live for TeamTendenciesCard backwards
-- compat. Cutover to universal reads is a follow-up.
--
-- Rows per (sport, team, season, market, filter):
--   market: 'spread' | 'total' | 'ml'
--   filter: 'overall' | 'l10' | 'l5' | 'home' | 'road' | 'as_fav' | 'as_dog'
--   wins   = spread: team covered · ml: team won SU · total: game went OVER
--   losses = spread: team failed · ml: team lost SU · total: game went UNDER
--   pushes = spread/total: push · ml: (always 0 — no ties)
--   games  = wins + losses + pushes
--
-- Coverage this migration: MLB + NCAAF (user's test sports for Tier 1).
-- NFL/NBA/NCAAB/NHL blocks queued for follow-up migrations as each
-- pipeline gets wired to call refresh.

DROP MATERIALIZED VIEW IF EXISTS public.team_situational_records;

CREATE MATERIALIZED VIEW public.team_situational_records AS
WITH all_games AS (
  -- ═══ MLB · home perspective ═══
  SELECT
    'MLB'::TEXT AS sport, game_id, season, game_date,
    home_team AS team, TRUE AS is_home,
    close_spread AS spread_line,
    (close_spread IS NOT NULL AND close_spread < 0) AS is_fav,
    home_win AS won_su,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won'
      WHEN 'away_covered' THEN 'lost'
      WHEN 'push'         THEN 'push'
      ELSE NULL
    END AS spread_res,
    LOWER(total_result) AS total_res
  FROM public.mlb_game_results WHERE home_score IS NOT NULL

  UNION ALL
  -- MLB · away perspective
  SELECT
    'MLB'::TEXT AS sport, game_id, season, game_date,
    away_team AS team, FALSE AS is_home,
    -close_spread AS spread_line,
    (close_spread IS NOT NULL AND close_spread > 0) AS is_fav,
    (NOT home_win) AS won_su,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost'
      WHEN 'away_covered' THEN 'won'
      WHEN 'push'         THEN 'push'
      ELSE NULL
    END AS spread_res,
    LOWER(total_result) AS total_res
  FROM public.mlb_game_results WHERE home_score IS NOT NULL

  UNION ALL
  -- ═══ NCAAF · home perspective ═══
  SELECT
    'NCAAF'::TEXT AS sport, game_id, season, game_date,
    home_team AS team, TRUE AS is_home,
    close_spread AS spread_line,
    (close_spread IS NOT NULL AND close_spread < 0) AS is_fav,
    home_win AS won_su,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'won'
      WHEN 'away_covered' THEN 'lost'
      WHEN 'push'         THEN 'push'
      ELSE NULL
    END AS spread_res,
    LOWER(total_result) AS total_res
  FROM public.ncaaf_game_results
  WHERE home_score IS NOT NULL AND COALESCE(neutral_site, FALSE) = FALSE

  UNION ALL
  -- NCAAF · away perspective
  SELECT
    'NCAAF'::TEXT AS sport, game_id, season, game_date,
    away_team AS team, FALSE AS is_home,
    -close_spread AS spread_line,
    (close_spread IS NOT NULL AND close_spread > 0) AS is_fav,
    (NOT home_win) AS won_su,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost'
      WHEN 'away_covered' THEN 'won'
      WHEN 'push'         THEN 'push'
      ELSE NULL
    END AS spread_res,
    LOWER(total_result) AS total_res
  FROM public.ncaaf_game_results
  WHERE home_score IS NOT NULL AND COALESCE(neutral_site, FALSE) = FALSE

  -- FUTURE: NFL / NBA / NCAAB / NHL blocks land here as each pipeline
  -- wires refresh_team_situational_records into its resolver step.
),

-- Enrich with per-team recency ranking so L10/L5 filters work
seq_enriched AS (
  SELECT
    *,
    ROW_NUMBER() OVER (PARTITION BY sport, team, season ORDER BY game_date DESC, game_id) AS seq_desc
  FROM all_games
),

-- ═══ Aggregate per (sport, team, season, market, filter) ═══
-- Each filter is a distinct SELECT block; UNION ALL them all.
-- Wins/losses/pushes semantics per market — see header comment.
agg AS (

  -- ── MARKET: SPREAD ──
  -- overall
  SELECT sport, team, season, 'spread'::TEXT AS market, 'overall'::TEXT AS filter,
    COUNT(*) FILTER (WHERE spread_res='won')  AS wins,
    COUNT(*) FILTER (WHERE spread_res='lost') AS losses,
    COUNT(*) FILTER (WHERE spread_res='push') AS pushes
  FROM seq_enriched WHERE spread_res IS NOT NULL GROUP BY sport, team, season
  UNION ALL
  -- l10
  SELECT sport, team, season, 'spread', 'l10',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND seq_desc <= 10 GROUP BY sport, team, season
  UNION ALL
  -- l5
  SELECT sport, team, season, 'spread', 'l5',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND seq_desc <= 5 GROUP BY sport, team, season
  UNION ALL
  -- home
  SELECT sport, team, season, 'spread', 'home',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND is_home GROUP BY sport, team, season
  UNION ALL
  -- road
  SELECT sport, team, season, 'spread', 'road',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND NOT is_home GROUP BY sport, team, season
  UNION ALL
  -- as_fav
  SELECT sport, team, season, 'spread', 'as_fav',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND is_fav GROUP BY sport, team, season
  UNION ALL
  -- as_dog
  SELECT sport, team, season, 'spread', 'as_dog',
    COUNT(*) FILTER (WHERE spread_res='won'),
    COUNT(*) FILTER (WHERE spread_res='lost'),
    COUNT(*) FILTER (WHERE spread_res='push')
  FROM seq_enriched WHERE spread_res IS NOT NULL AND NOT is_fav GROUP BY sport, team, season

  -- ── MARKET: TOTAL ── (wins=over, losses=under, pushes=push)
  UNION ALL
  SELECT sport, team, season, 'total', 'overall',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'l10',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND seq_desc <= 10 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'l5',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND seq_desc <= 5 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'home',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'road',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND NOT is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'as_fav',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND is_fav GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'total', 'as_dog',
    COUNT(*) FILTER (WHERE total_res='over'),
    COUNT(*) FILTER (WHERE total_res='under'),
    COUNT(*) FILTER (WHERE total_res='push')
  FROM seq_enriched WHERE total_res IS NOT NULL AND NOT is_fav GROUP BY sport, team, season

  -- ── MARKET: ML ── (wins=SU win, losses=SU loss, pushes=0 always)
  UNION ALL
  SELECT sport, team, season, 'ml', 'overall',
    COUNT(*) FILTER (WHERE won_su IS TRUE),
    COUNT(*) FILTER (WHERE won_su IS FALSE),
    0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'l10',
    COUNT(*) FILTER (WHERE won_su IS TRUE),
    COUNT(*) FILTER (WHERE won_su IS FALSE),
    0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND seq_desc <= 10 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'l5',
    COUNT(*) FILTER (WHERE won_su IS TRUE),
    COUNT(*) FILTER (WHERE won_su IS FALSE),
    0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND seq_desc <= 5 GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'home',
    COUNT(*) FILTER (WHERE won_su IS TRUE),
    COUNT(*) FILTER (WHERE won_su IS FALSE),
    0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'road',
    COUNT(*) FILTER (WHERE won_su IS TRUE),
    COUNT(*) FILTER (WHERE won_su IS FALSE),
    0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND NOT is_home GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'as_fav',
    COUNT(*) FILTER (WHERE won_su IS TRUE),
    COUNT(*) FILTER (WHERE won_su IS FALSE),
    0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND is_fav GROUP BY sport, team, season
  UNION ALL
  SELECT sport, team, season, 'ml', 'as_dog',
    COUNT(*) FILTER (WHERE won_su IS TRUE),
    COUNT(*) FILTER (WHERE won_su IS FALSE),
    0::BIGINT
  FROM seq_enriched WHERE won_su IS NOT NULL AND NOT is_fav GROUP BY sport, team, season
)
SELECT
  sport, team, season, market, filter,
  wins, losses, pushes,
  (wins + losses + pushes) AS games,
  NOW() AS refreshed_at
FROM agg
WHERE (wins + losses + pushes) > 0;  -- skip empty slices


-- Unique index required for REFRESH CONCURRENTLY + primary lookup pattern
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_situational_records_uq
  ON public.team_situational_records (sport, team, season, market, filter);

CREATE INDEX IF NOT EXISTS idx_team_situational_records_lookup
  ON public.team_situational_records (sport, team, season);


-- ═══════════════════════════════════════════════════════════════════════
-- Refresh helper — call from each sport's pipeline resolver AFTER
-- {sport}_game_results is updated. Cross-sport in one shot (small view
-- footprint — 21 rows per team per season max).
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.refresh_team_situational_records()
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY public.team_situational_records;
END $$;

NOTIFY pgrst, 'reload schema';
