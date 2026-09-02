-- mlb_team_pitching persistent table (2026-09-01)
--
-- Fills the last gap in MLB team_stats_rolling coverage. Team pitching
-- stats (ERA/WHIP/K per 9/BB per 9/HR per 9) have been pulled LIVE
-- inside generate_mlb_game_reads.py:157-205 (fetch_team_pitching_snapshots)
-- every cron tick — 30 MLB StatsAPI calls per generation, never persisted.
--
-- This table stores the same stats nightly so:
--   1. team_stats_rolling matview can rank + display ERA/WHIP alongside
--      batting stats (currently MLB only has 13 batting/bullpen stats,
--      no team-wide pitching)
--   2. generate_mlb_game_reads.py can eventually read from this table
--      instead of re-fetching every cron (30 API calls saved per tick)
--
-- Populated by mlb_pipeline/mlb_team_pitching_pull.py — same fetch
-- logic as generate_mlb_game_reads, adapted to write to Supabase.

CREATE TABLE IF NOT EXISTS public.mlb_team_pitching (
  team          TEXT    NOT NULL,
  season        INT     NOT NULL,
  -- Raw counting stats from MLB StatsAPI team season aggregate
  era           NUMERIC(5, 2),
  whip          NUMERIC(4, 2),
  baa           NUMERIC(5, 3),   -- batting avg against
  k             INT,             -- strikeouts total
  bb            INT,             -- walks total
  hr_allowed    INT,
  ip            NUMERIC(6, 1),   -- innings pitched
  -- Derived per-9 rates (computed by puller; NULL if ip insufficient)
  k_per_9       NUMERIC(4, 2),
  bb_per_9      NUMERIC(4, 2),
  hr_per_9      NUMERIC(4, 2),
  k_bb_ratio    NUMERIC(5, 2),   -- K/BB — dimensionless, HIGHER better
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (team, season)
);

CREATE INDEX IF NOT EXISTS idx_mlb_team_pitching_season
  ON public.mlb_team_pitching (season DESC, era ASC);

NOTIFY pgrst, 'reload schema';
