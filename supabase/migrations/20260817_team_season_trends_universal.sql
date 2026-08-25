-- Universal team season trends table (2026-08-17).
--
-- ONE table for teamrankings.com ATS + O/U aggregates across all sports.
-- Same shape works for NCAAB / NCAAF / NFL / NBA / MLB. Signals per sport
-- read via ctx enrichment (same pattern per sport).
--
-- Replaces the sport-specific ncaab_team_trends approach from earlier
-- migration (kept in place for backward compat; new sports use this).
--
-- Populated by pull_teamrankings_trends.py --sport <SPORT>
-- Enriched onto per-game ctx rows by enrich_team_trends.py --sport <SPORT>

CREATE TABLE IF NOT EXISTS public.team_season_trends (
  id                BIGSERIAL PRIMARY KEY,
  sport             TEXT NOT NULL,           -- 'NCAAB' | 'NCAAF' | 'NFL' | 'NBA' | 'MLB'
  team              TEXT NOT NULL,           -- teamrankings display name
  season            TEXT NOT NULL,           -- '2025-26' (basketball/hockey), '2025' (football/mlb)
  ats_wins          INT,
  ats_losses        INT,
  ats_pushes        INT,
  cover_pct         NUMERIC,
  ats_plus_minus    NUMERIC,
  mov               NUMERIC,
  ou_overs          INT,
  ou_unders         INT,
  ou_pushes         INT,
  over_pct          NUMERIC,
  total_plus_minus  NUMERIC,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(sport, team, season)
);

CREATE INDEX IF NOT EXISTS idx_team_season_trends_sport_season
  ON public.team_season_trends (sport, season);

-- ─── Season-tendency columns on per-sport game_context tables ─────────
-- NCAAF
ALTER TABLE public.ncaaf_game_context
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

-- NFL (same shape)
ALTER TABLE public.nfl_game_context
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

-- MLB
ALTER TABLE public.mlb_game_context
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

-- NBA (table may not exist yet — guarded)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='nba_game_context') THEN
    ALTER TABLE public.nba_game_context
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
  END IF;
END $$;

-- RLS
ALTER TABLE public.team_season_trends ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.team_season_trends;
CREATE POLICY public_read ON public.team_season_trends FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS service_role_write ON public.team_season_trends;
CREATE POLICY service_role_write ON public.team_season_trends FOR ALL TO service_role USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
