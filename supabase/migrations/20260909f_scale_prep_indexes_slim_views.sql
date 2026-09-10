-- 2026-09-09: Scaling prep for 500-user launch capacity.
--
-- Two-part fix:
--
-- 1. INDEXES on hot-path fetch queries.
--    fetchMLBContext in app/index.tsx runs up to 5 sequential SELECT * on
--    mlb_game_context filtered by (game_date, home_team, away_team) and
--    (game_date, home_team ILIKE, away_team ILIKE). Without indexes these
--    do sequential scans of the full table (~150KB+ per row across all
--    JSONB fields). On a 500-user click spike that's crushing CPU.
--
--    Same pattern in nfl_game_context, ncaaf_game_context, and jerry_cache
--    (cache_key lookups).
--
-- 2. SLIM VIEWS with only display-critical columns.
--    mlb_game_context SELECT * = 17KB/row. The numbers panel display
--    needs maybe 20 columns = ~2KB/row. 88% egress reduction.
--    App swap to slim views happens in v1.0.1 client — this creates
--    the views now so they're ready when client ships.
--
-- All non-destructive (CREATE IF NOT EXISTS + CREATE OR REPLACE VIEW).

-- Part 1: INDEXES for hot-path fetches
-- Note: CREATE INDEX CONCURRENTLY not supported inside migration txn.
-- IF NOT EXISTS is the safety net.

CREATE INDEX IF NOT EXISTS idx_mlb_ctx_date_teams
  ON mlb_game_context (game_date, home_team, away_team);

CREATE INDEX IF NOT EXISTS idx_mlb_ctx_date_home_lower
  ON mlb_game_context (game_date, LOWER(home_team));

CREATE INDEX IF NOT EXISTS idx_nfl_ctx_date_teams
  ON nfl_game_context (game_date, home_team, away_team);

CREATE INDEX IF NOT EXISTS idx_ncaaf_ctx_date_teams
  ON ncaaf_game_context (game_date, home_team, away_team);

CREATE INDEX IF NOT EXISTS idx_mlb_props_date_tier
  ON mlb_pipeline_props (game_date, tier);

CREATE INDEX IF NOT EXISTS idx_nfl_props_date_tier
  ON nfl_pipeline_props (game_date, tier);

-- jerry_cache is the biggest hot-path (every user hits it every load).
-- Already has unique index on cache_key; add composite for time-range queries.
CREATE INDEX IF NOT EXISTS idx_jerry_cache_key_fetched
  ON jerry_cache (cache_key, fetched_at DESC);


-- Part 2: SLIM VIEWS (~2KB/row instead of 17KB — 88% egress reduction)
-- Only columns the app actually renders. Heavy debug/audit JSONB columns
-- (sweat_breakdown, jerry_components, align_status, oddscrowd_snapshot,
-- ensemble_debug) excluded — those are for backend/analytics only.

CREATE OR REPLACE VIEW mlb_game_context_slim AS
SELECT
  game_id, game_date, home_team, away_team, kickoff_utc,
  close_spread, close_total, close_home_ml, close_away_ml,
  open_spread, open_total,
  home_ml_close, away_ml_close,
  projected_spread, projected_total,
  model_pred_home_runs, model_pred_away_runs, model_pred_total,
  panel_pred_home_pts, panel_pred_away_pts, panel_pred_total,
  primary_play, secondary_play, supplementary_plays,
  splits_summary,
  signal_confluence_net, signal_confluence_breakdown,
  cohort_tags,
  sweat_score, sweat_tier,
  home_team_stats_summary, away_team_stats_summary,
  home_pitcher, away_pitcher, pitcher_context,
  venue, temperature, weather,
  mc_probabilities,
  mc_high_conf_side, nrfi_ensemble_pick,
  home_lineup, away_lineup, lineup_confirmed,
  stats_source, season, season_type, week,
  fetched_at
FROM mlb_game_context;

CREATE OR REPLACE VIEW nfl_game_context_slim AS
SELECT
  game_id, game_date, kickoff_utc, home_team, away_team,
  close_spread, close_total, close_home_ml, close_away_ml,
  open_spread, open_total,
  projected_spread, projected_total,
  model_pred_home_points, model_pred_away_points,
  panel_pred_home_pts, panel_pred_away_pts, panel_pred_total,
  v3_spread, v3_total, v4_spread, v4_total, v4_confidence,
  primary_play, secondary_play,
  signal_confluence_net, signal_confluence_breakdown,
  splits_summary,
  cohort_tags,
  sweat_score, sweat_tier,
  home_team_stats_summary, away_team_stats_summary,
  temp, wind, roof,
  home_rest, away_rest, div_game,
  stats_source, season, season_type, week,
  fetched_at
FROM nfl_game_context;

CREATE OR REPLACE VIEW ncaaf_game_context_slim AS
SELECT
  game_id, game_date, home_team, away_team, kickoff_utc,
  close_spread, close_total, close_home_ml, close_away_ml,
  open_spread, open_total,
  projected_spread, projected_total,
  primary_play, secondary_play,
  splits_summary,
  cohort_tags,
  sweat_score, sweat_tier,
  home_team_stats_summary, away_team_stats_summary,
  home_sp_overall, away_sp_overall, sp_gap,
  home_sp_plus, away_sp_plus,
  home_ap_rank, away_ap_rank,
  temp, wind, dome, weather_source, neutral_site, conference_game,
  home_stats_blend_label, away_stats_blend_label,
  fetched_at
FROM ncaaf_game_context;

NOTIFY pgrst, 'reload schema';
