-- Add SP+ + returning production columns to ncaaf_game_context (2026-08-09).
--
-- SP+ (Bill Connelly composite rating) is the industry-standard college
-- football model. Fetched from CFBD API per season/team. Serves as
-- second-lens projection alongside EPA-matchup model (analog to MLB's
-- Panel or NFL's Panel + Matchup).
--
-- Returning production % (offensive + defensive returning talent from
-- prior season) is the strongest early-season variance-reducing prior.
-- Critical for Weeks 1-3 when current-season EPA is thin.
--
-- Conventions:
--   sp_plus_pred_spread — nflverse convention (positive = HOME favored),
--                          matches projected_spread in existing table.
--   sp_plus_pred_total  — total points prediction.
--   sp_plus_overall     — team's overall SP+ rating (higher = better).

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS sp_plus_pred_home_pts NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS sp_plus_pred_away_pts NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS sp_plus_pred_total NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS sp_plus_pred_spread NUMERIC(5,2),   -- positive = home fav
  ADD COLUMN IF NOT EXISTS home_sp_plus_overall NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS away_sp_plus_overall NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS home_sp_plus_off NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS home_sp_plus_def NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS away_sp_plus_off NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS away_sp_plus_def NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS home_returning_production_off NUMERIC(5,2), -- 0-1 fraction
  ADD COLUMN IF NOT EXISTS home_returning_production_def NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS away_returning_production_off NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS away_returning_production_def NUMERIC(5,2);


-- Per-team storage tables (season-level, refreshed weekly from CFBD)
CREATE TABLE IF NOT EXISTS public.ncaaf_sp_plus (
  id BIGSERIAL PRIMARY KEY,
  team TEXT NOT NULL,               -- CFBD canonical team name
  season INT NOT NULL,
  sp_plus_overall NUMERIC(6,2),
  sp_plus_off NUMERIC(6,2),
  sp_plus_def NUMERIC(6,2),
  sp_plus_special NUMERIC(6,2),
  rank_overall INT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (team, season)
);

CREATE TABLE IF NOT EXISTS public.ncaaf_returning_production (
  id BIGSERIAL PRIMARY KEY,
  team TEXT NOT NULL,
  season INT NOT NULL,
  returning_offense_pct NUMERIC(5,2),      -- fraction of last season's offensive production returning
  returning_defense_pct NUMERIC(5,2),
  returning_pass_yards NUMERIC(6,2),
  returning_rush_yards NUMERIC(6,2),
  returning_receiving_yards NUMERIC(6,2),
  returning_ppa NUMERIC(6,2),               -- overall predicted points added returning
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (team, season)
);

CREATE INDEX IF NOT EXISTS ncaaf_sp_plus_team_season_idx
  ON public.ncaaf_sp_plus (team, season DESC);
CREATE INDEX IF NOT EXISTS ncaaf_rp_team_season_idx
  ON public.ncaaf_returning_production (team, season DESC);

NOTIFY pgrst, 'reload schema';
