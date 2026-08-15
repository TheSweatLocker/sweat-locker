-- Savant pitcher batted-ball + SIERA (2026-08-15).
--
-- Full SIERA rebuild per project_advanced_metrics_backtest_815 —
-- simplified formula lost by 23% vs FIP-proxy xERA. Full formula
-- requires per-pitcher GB%/FB%/PU% split from Baseball Savant.
--
-- Endpoint: https://baseballsavant.mlb.com/leaderboard/custom
--           ?year=YYYY&type=pitcher&min=50
--           &selections=k_percent,bb_percent,groundballs_percent,
--                       flyballs_percent,linedrives_percent,popups_percent
--
-- ~234 pitchers per pull. Runs nightly, upserts on (mlb_id, season).
--
-- Full SIERA formula (from FanGraphs):
--   SIERA = 6.145
--         - 16.986 * (K/PA)
--         + 11.434 * (BB/PA)
--         - 1.858  * (GB%)
--         + 7.653  * (K/PA)^2
--         + interaction_term(GB, FB, PU)
--         + 10.130 * (K/PA) * (GB% - FB% - PU%)
--
-- Where interaction:
--   diff = GB% - FB% - PU%
--   if diff < 0: -6.664 * diff^2
--   else:        +6.664 * diff^2

CREATE TABLE IF NOT EXISTS public.savant_pitcher_stats (
  id             BIGSERIAL PRIMARY KEY,
  mlb_id         INTEGER NOT NULL,
  pitcher_name   TEXT NOT NULL,
  season         INTEGER NOT NULL,

  -- Raw Savant fields
  k_pct          NUMERIC,
  bb_pct         NUMERIC,
  gb_pct         NUMERIC,
  fb_pct         NUMERIC,
  ld_pct         NUMERIC,
  pu_pct         NUMERIC,

  -- Derived SIERA (full formula)
  siera_full     NUMERIC,

  -- Bookkeeping
  fetched_at     TIMESTAMPTZ DEFAULT NOW(),
  updated_at     TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE (mlb_id, season)
);

CREATE INDEX IF NOT EXISTS savant_pitcher_stats_name_idx
  ON public.savant_pitcher_stats (pitcher_name, season DESC);

NOTIFY pgrst, 'reload schema';
