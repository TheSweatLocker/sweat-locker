-- team_recent_games matview (2026-09-01)
--
-- Cross-sport per-team recent-games rollup. One row per (sport, team, game)
-- from a TEAM-PERSPECTIVE view: MIA @ STAN produces one row for team=MIA
-- (opp=STAN, is_home=FALSE) and one row for team=STAN (opp=MIA, is_home=TRUE).
--
-- Powers the Recent Schedule card on game detail (H2H tab + per-team tabs)
-- across every sport. App reads with:
--   WHERE sport = ? AND team = ? [AND opp = ?] ORDER BY seq LIMIT N
--
-- Design:
--   - Universal matview with `sport` column (not per-sport) so app has ONE
--     query pattern and new sports drop in by adding a UNION block
--   - Team perspective on spread_result normalized to 'won'/'lost'/'push'
--     (was 'home_covered'/'away_covered' — team-agnostic + confusing)
--   - total_result stays 'over'/'under'/'push' (team-agnostic; both teams
--     see same result)
--   - MLB total_runs and NCAAF total_points aliased to common `total_score`
--   - MLB total_result casing normalized via LOWER() (MLB writes 'Over'/'Under',
--     NCAAF writes 'over'/'under' — hostile inconsistency in source data)
--   - Neutral-site NCAAF games included (both teams get is_home=FALSE marker
--     via new is_neutral col) so H2H like bowl games / Missouri kickoff at
--     neutral venues still surface. The situational_records matview EXCLUDES
--     neutral for H/R splits, which is different semantics — Recent Schedule
--     shows the game happened; Situational partitions by venue role.
--
-- Coverage this migration: MLB + NCAAF (user's test sports for 2026-09-01
-- Tier 1 rollout). NFL / NBA / NCAAB / NHL blocks queued for follow-up
-- migrations as each pipeline gets wired to call refresh.
--
-- Refresh:
--   SELECT public.refresh_team_recent_games();
--   (uses CONCURRENTLY — unique index below is required)
--
-- Idempotent: DROP + CREATE at top so shape changes re-apply cleanly.

DROP MATERIALIZED VIEW IF EXISTS public.team_recent_games;

CREATE MATERIALIZED VIEW public.team_recent_games AS
WITH all_games AS (

  -- ═══════════════════════════════════════════════════════════════════
  -- MLB · home perspective
  -- ═══════════════════════════════════════════════════════════════════
  SELECT
    'MLB'::TEXT AS sport,
    game_id, season, game_date,
    home_team AS team, away_team AS opp,
    TRUE AS is_home,
    FALSE AS is_neutral,
    home_score AS score_us,
    away_score AS score_them,
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
  WHERE home_score IS NOT NULL

  UNION ALL

  -- MLB · away perspective
  SELECT
    'MLB'::TEXT AS sport,
    game_id, season, game_date,
    away_team AS team, home_team AS opp,
    FALSE AS is_home,
    FALSE AS is_neutral,
    away_score AS score_us,
    home_score AS score_them,
    (NOT home_win) AS won,
    -close_spread AS spread_line,   -- flip: home -3 becomes away +3 for team-perspective
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost'
      WHEN 'away_covered' THEN 'won'
      WHEN 'push'         THEN 'push'
      ELSE NULL
    END AS spread_result,
    close_total AS total_line,
    LOWER(total_result) AS total_result,
    total_runs AS total_score
  FROM public.mlb_game_results
  WHERE home_score IS NOT NULL

  UNION ALL

  -- ═══════════════════════════════════════════════════════════════════
  -- NCAAF · home perspective (neutral-site rows kept — see design note)
  -- ═══════════════════════════════════════════════════════════════════
  SELECT
    'NCAAF'::TEXT AS sport,
    game_id, season, game_date,
    home_team AS team, away_team AS opp,
    TRUE AS is_home,
    COALESCE(neutral_site, FALSE) AS is_neutral,
    home_score AS score_us,
    away_score AS score_them,
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
    total_points AS total_score
  FROM public.ncaaf_game_results
  WHERE home_score IS NOT NULL

  UNION ALL

  -- NCAAF · away perspective
  SELECT
    'NCAAF'::TEXT AS sport,
    game_id, season, game_date,
    away_team AS team, home_team AS opp,
    FALSE AS is_home,
    COALESCE(neutral_site, FALSE) AS is_neutral,
    away_score AS score_us,
    home_score AS score_them,
    (NOT home_win) AS won,
    -close_spread AS spread_line,
    CASE LOWER(spread_result)
      WHEN 'home_covered' THEN 'lost'
      WHEN 'away_covered' THEN 'won'
      WHEN 'push'         THEN 'push'
      ELSE NULL
    END AS spread_result,
    close_total AS total_line,
    LOWER(total_result) AS total_result,
    total_points AS total_score
  FROM public.ncaaf_game_results
  WHERE home_score IS NOT NULL

  -- ═══════════════════════════════════════════════════════════════════
  -- FUTURE: NFL / NBA / NCAAB / NHL blocks land here as each pipeline
  -- wires refresh_team_recent_games into its resolver step.
  -- ═══════════════════════════════════════════════════════════════════
)
SELECT
  sport, team,
  game_id, season, game_date, opp,
  is_home, is_neutral,
  score_us, score_them, won,
  spread_line, spread_result,
  total_line, total_result, total_score,
  -- seq = 1 is most recent game per team (per sport)
  ROW_NUMBER() OVER (
    PARTITION BY sport, team
    ORDER BY game_date DESC, game_id
  ) AS seq
FROM all_games;

-- Unique index required for REFRESH CONCURRENTLY. Also drives the primary
-- lookup pattern (sport, team, game_id) — plus the composite for seq scans.
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_recent_games_uq
  ON public.team_recent_games (sport, team, game_id);

CREATE INDEX IF NOT EXISTS idx_team_recent_games_seq
  ON public.team_recent_games (sport, team, seq);

-- Secondary index for H2H lookups (team + opp)
CREATE INDEX IF NOT EXISTS idx_team_recent_games_h2h
  ON public.team_recent_games (sport, team, opp, game_date DESC);


-- ═══════════════════════════════════════════════════════════════════════
-- Refresh helper — called from each sport's pipeline resolver step AFTER
-- {sport}_game_results is updated. CONCURRENTLY keeps reads unblocked
-- during the swap.
--
-- No sport arg needed — the whole view refreshes together in one shot
-- because the source union is small (~30k rows max cross-sport).
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.refresh_team_recent_games()
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY public.team_recent_games;
END $$;

NOTIFY pgrst, 'reload schema';
