-- nfl_team_defense_stats: derived opponent-side stats per team per season
-- (2026-08-09 · NFL Phase 2).
--
-- nflverse gives us offensive team stats but NOT defensive. This table
-- is populated by nfl_team_defense_backfill.py which iterates over
-- nfl_game_results, aggregating what each team allowed based on the
-- opposing team's offense stats.
--
-- Used by nfl_game_context.compute_projections() Phase 2 rewrite to
-- generate per-matchup total projections instead of the previous flat
-- BASE_TOTAL=44.5 for every game.

CREATE TABLE IF NOT EXISTS public.nfl_team_defense_stats (
  id BIGSERIAL PRIMARY KEY,
  team TEXT NOT NULL,
  season INT NOT NULL,
  season_type TEXT NOT NULL DEFAULT 'reg',

  games INT,
  def_ppg NUMERIC(5,2),                -- points allowed per game
  def_ypg NUMERIC(6,2),                -- yards allowed per game
  def_pass_epa_allowed NUMERIC(6,4),   -- avg opponent pass_epa per game
  def_rush_epa_allowed NUMERIC(6,4),   -- avg opponent rush_epa per game

  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (team, season, season_type)
);

CREATE INDEX IF NOT EXISTS nfl_def_stats_team_season_idx
  ON public.nfl_team_defense_stats (team, season DESC);

NOTIFY pgrst, 'reload schema';
