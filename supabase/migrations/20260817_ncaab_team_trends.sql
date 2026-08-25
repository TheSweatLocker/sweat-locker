-- NCAAB team season trends (2026-08-17).
--
-- Unblocks NCAAB tendencies port using season-level ATS + O/U aggregates
-- scraped from teamrankings.com (per-game backfill isn't available;
-- season aggregates are the practical alternative and still predictive).
--
-- Data source: pull_ncaab_teamrankings_trends.py runs weekly during
-- NCAAB season (Nov-Apr). Season-level stats update slowly so weekly
-- refresh is plenty; more often just burns their bandwidth.
--
-- Signal shape: instead of L10 rolling like MLB/NFL/NCAAF/NHL, NCAAB
-- signals fire on SEASON aggregate:
--   * team_ats_hot: season cover % >= 60
--   * team_ats_cold: season cover % <= 42
--   * team_over_trend: season over % >= 60
--   * team_under_trend: season over % <= 40
-- These are STICKIER than rolling (season aggregates don't whip around
-- game to game) but still predictive per teamrankings' own trend
-- analyses.

CREATE TABLE IF NOT EXISTS public.ncaab_team_trends (
  id              BIGSERIAL PRIMARY KEY,
  team            TEXT NOT NULL,           -- teamrankings display name (e.g. 'Arizona')
  season          TEXT NOT NULL,           -- '2025-26'
  ats_wins        INT,
  ats_losses      INT,
  ats_pushes      INT,
  cover_pct       NUMERIC,                 -- 0-100
  ats_plus_minus  NUMERIC,                 -- per-game ATS margin
  mov             NUMERIC,                 -- avg MOV
  ou_overs        INT,
  ou_unders       INT,
  ou_pushes       INT,
  over_pct        NUMERIC,                 -- 0-100
  total_plus_minus NUMERIC,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(team, season)
);

CREATE INDEX IF NOT EXISTS idx_ncaab_trends_season ON public.ncaab_team_trends (season);

-- Add season-tendency lookup columns to ncaab_game_context so signals
-- can read via ctx fields (matches MLB/NFL pattern where signals read
-- ctx.home_ats_last10 etc.). Enrichment populates these from the trends
-- table when game_context is built.
ALTER TABLE public.ncaab_game_context
  ADD COLUMN IF NOT EXISTS home_season_cover_pct      NUMERIC,
  ADD COLUMN IF NOT EXISTS home_season_ats_wins       INT,
  ADD COLUMN IF NOT EXISTS home_season_ats_losses     INT,
  ADD COLUMN IF NOT EXISTS home_season_over_pct       NUMERIC,
  ADD COLUMN IF NOT EXISTS home_season_ou_overs       INT,
  ADD COLUMN IF NOT EXISTS home_season_ou_unders      INT,
  ADD COLUMN IF NOT EXISTS away_season_cover_pct      NUMERIC,
  ADD COLUMN IF NOT EXISTS away_season_ats_wins       INT,
  ADD COLUMN IF NOT EXISTS away_season_ats_losses     INT,
  ADD COLUMN IF NOT EXISTS away_season_over_pct       NUMERIC,
  ADD COLUMN IF NOT EXISTS away_season_ou_overs       INT,
  ADD COLUMN IF NOT EXISTS away_season_ou_unders      INT,
  ADD COLUMN IF NOT EXISTS team_trends_updated_at     TIMESTAMPTZ;

-- RLS: read-open for anon, service_role writes (matches tightened pattern)
ALTER TABLE public.ncaab_team_trends ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.ncaab_team_trends;
CREATE POLICY public_read ON public.ncaab_team_trends FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS service_role_write ON public.ncaab_team_trends;
CREATE POLICY service_role_write ON public.ncaab_team_trends FOR ALL TO service_role USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
