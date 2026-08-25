-- nfl_player_projections: per-week fantasy stat projections per player
-- (2026-08-09 · NFL Panel model foundation).
--
-- Aggregating individual player projections to the team level gives us
-- a "Panel model" analog for NFL — same shape as MLB Panel where
-- opposing lineup wRC+ implies runs. Here: sum(QB xPassYd + RB xRushYd
-- + WR xRecYd + K xFG + DEF xSacks/INTs) → convert to team pts.
--
-- Sources:
--   - Sleeper API (primary — free, well-documented)
--   - ESPN Fantasy (secondary — for ensemble)
--   - PFF (paid, future addition)
--
-- One row per (source, season, week, player_id). Sources may disagree —
-- team aggregator averages across sources for consensus.

CREATE TABLE IF NOT EXISTS public.nfl_player_projections (
  id BIGSERIAL PRIMARY KEY,

  source TEXT NOT NULL,                    -- 'sleeper' | 'espn_fantasy' | 'pff'
  season INT NOT NULL,
  week INT NOT NULL,
  season_type TEXT NOT NULL DEFAULT 'reg', -- 'reg' | 'pre' | 'post'

  player_id TEXT NOT NULL,                  -- source-specific ID
  player_name TEXT,
  team TEXT,                                -- 2-3 char team abbrev
  position TEXT,                            -- QB / RB / WR / TE / K / DEF / DST

  -- Fantasy stat projections (nulls where not applicable)
  proj_pass_yds NUMERIC(6,2),
  proj_pass_tds NUMERIC(4,2),
  proj_pass_ints NUMERIC(4,2),
  proj_pass_attempts NUMERIC(5,2),

  proj_rush_yds NUMERIC(6,2),
  proj_rush_tds NUMERIC(4,2),
  proj_rush_attempts NUMERIC(5,2),

  proj_rec_yds NUMERIC(6,2),
  proj_rec_tds NUMERIC(4,2),
  proj_receptions NUMERIC(4,2),
  proj_targets NUMERIC(5,2),

  proj_fg_made NUMERIC(4,2),
  proj_xp_made NUMERIC(4,2),

  -- Team defense projections (when position = 'DEF' or 'DST')
  proj_def_sacks NUMERIC(4,2),
  proj_def_ints NUMERIC(4,2),
  proj_def_fumbles NUMERIC(4,2),
  proj_def_tds NUMERIC(4,2),
  proj_def_pts_allowed NUMERIC(5,2),

  -- Composite fantasy pts (PPR + standard variants)
  proj_fantasy_pts NUMERIC(6,2),            -- source-native PPR
  proj_fantasy_pts_std NUMERIC(6,2),        -- standard (no PPR) if source provides

  -- Injury / status flag from source
  status TEXT,                              -- Active / Q / D / IR / OUT / etc.

  pulled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (source, season, week, player_id)
);

CREATE INDEX IF NOT EXISTS nfl_player_proj_week_team_idx
  ON public.nfl_player_projections (season, week, team);
CREATE INDEX IF NOT EXISTS nfl_player_proj_source_week_idx
  ON public.nfl_player_projections (source, season, week);

NOTIFY pgrst, 'reload schema';
