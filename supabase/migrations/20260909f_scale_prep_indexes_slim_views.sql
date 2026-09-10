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
--    mlb_game_context SELECT * = 17KB/row across 317 columns. Numbers panel
--    display needs ~40 columns = ~2KB/row. 88% egress reduction.
--    App swap to slim views happens in v1.0.1 client — this creates
--    the views now so they're ready when client ships.
--
-- 2026-09-09 v2: rebuilt after v1 failed with 42703 kickoff_utc missing.
-- Column lists verified against live tables:
--   MLB: 317 cols (no kickoff_utc — uses game_date only; uses home_ml_close
--        not close_home_ml; no secondary_play/team_stats_summary/cohort_tags)
--   NFL: 197 cols (has kickoff_utc, close_home_ml, team_stats_summary, cohort_tags)
--   NCAAF: 177 cols (has kickoff_utc, close_home_ml, team_stats_summary,
--          cohort_tags, blend_label, sp_plus_pred_* cols)
--
-- All non-destructive (CREATE IF NOT EXISTS + CREATE OR REPLACE VIEW).

-- Part 1: INDEXES for hot-path fetches

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


-- Part 2: SLIM VIEWS — 88% egress reduction

CREATE OR REPLACE VIEW mlb_game_context_slim AS
SELECT
  game_id, game_date, home_team, away_team,
  close_spread, close_total, open_spread, open_total,
  home_ml_close, away_ml_close, home_ml_odds, away_ml_odds,
  home_ml_open, away_ml_open,
  projected_spread, projected_total,
  model_pred_home_runs, model_pred_away_runs, model_pred_total, model_pred_spread,
  jerry_pred_home_runs, jerry_pred_away_runs, jerry_pred_total, jerry_pred_spread,
  panel_implied_margin, panel_implied_total,
  primary_play, primary_play_computed_at,
  splits_summary,
  signal_confluence_net, signal_confluence_breakdown,
  signal_confluence_v2_net, signal_confluence_v2_breakdown,
  sweat_score, sweat_tier, sweat_tier_max, sweat_tier_locked_at,
  home_pitcher, away_pitcher, pitcher_context,
  venue, temperature, wind_speed, wind_direction, wind_blowing_in, precipitation,
  is_dome, park_run_factor,
  mc_probabilities, mc_high_conf_side, mc_high_conf_flag, mc_high_conf_pct,
  nrfi_ensemble_pick, nrfi_ensemble_tier, nrfi_ensemble_conf,
  home_lineup, away_lineup, lineup_confirmed,
  matched_patterns, consensus_fade_flag, consensus_fade_side, consensus_fade_pct,
  babip_regression_flag,
  umpire, umpire_over_rate, umpire_run_factor,
  data_completeness, model_confidence,
  fetched_at
FROM mlb_game_context;

CREATE OR REPLACE VIEW nfl_game_context_slim AS
SELECT
  game_id, game_date, kickoff_utc, home_team, away_team,
  close_spread, close_total, close_home_ml, close_away_ml,
  home_ml_close, home_ml_odds, home_ml_open,
  away_ml_odds,
  open_spread, open_total,
  projected_spread, projected_total,
  model_pred_home_points, model_pred_away_points,
  panel_pred_home_pts, panel_pred_away_pts, panel_pred_total, panel_confidence,
  v3_spread, v3_total, v4_spread, v4_total, v4_confidence,
  primary_play,
  signal_confluence_net, signal_confluence_breakdown,
  splits_summary,
  cohort_tags,
  sweat_score, sweat_tier,
  home_team_stats_summary, away_team_stats_summary,
  home_off_rating, away_off_rating,
  home_def_ppg, away_def_ppg,
  home_def_pass_ypg, away_def_pass_ypg,
  home_def_rush_ypg, away_def_rush_ypg,
  home_qb_name, away_qb_name,
  home_madden_ovr, away_madden_ovr,
  home_top100_count, away_top100_count,
  temp, wind, roof, surface,
  home_rest, away_rest, div_game,
  season, season_type, week,
  matched_patterns, updated_at, computed_at
FROM nfl_game_context;

CREATE OR REPLACE VIEW ncaaf_game_context_slim AS
SELECT
  game_id, game_date, kickoff_utc, home_team, away_team,
  close_spread, close_total, close_home_ml, close_away_ml,
  open_spread, open_total,
  projected_spread, projected_total,
  model_pred_home_points, model_pred_away_points,
  sp_plus_pred_home_pts, sp_plus_pred_away_pts, sp_plus_pred_total, sp_plus_pred_spread,
  sp_plus_matchup_total,
  primary_play,
  splits_summary,
  cohort_tags,
  sweat_score, sweat_tier,
  home_team_stats_summary, away_team_stats_summary,
  home_stats_blend_label, away_stats_blend_label,
  home_games_played, away_games_played,
  home_sp_overall, away_sp_overall, sp_gap,
  home_sp_plus, away_sp_plus, home_sp_plus_overall, away_sp_plus_overall,
  home_sp_plus_off, away_sp_plus_off, home_sp_plus_def, away_sp_plus_def,
  home_ap_rank, away_ap_rank,
  home_talent, away_talent, home_sor, away_sor,
  home_returning_production, away_returning_production,
  signal_confluence_net, signal_confluence_breakdown,
  temp, wind, dome, weather_source, neutral_site, conference_game,
  season, season_type, week,
  matched_patterns, updated_at, computed_at
FROM ncaaf_game_context;

NOTIFY pgrst, 'reload schema';
