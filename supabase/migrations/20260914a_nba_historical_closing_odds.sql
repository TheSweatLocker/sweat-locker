-- NBA historical closing odds (2026-09-14).
--
-- Destination table for backfilling historical NBA closing lines against
-- the 1,324 games in nba_game_results (2024-10-22 → 2025-06-22, one full
-- 2024-25 regular + playoffs season). Enables NBA LR training + signal
-- validation before Oct 22 season opener.
--
-- Shape mirrors nhl_historical_closing_odds (20260820) minus puckline
-- (NBA has no ±1.5 sport-specific market), plus close_spread which is
-- the primary NBA market. Multi-source via `source` tag.
--
-- Key: (game_date, away_team, home_team) triple (NBA doesn't have a
-- stable per-game URL like NHL's hockey-reference boxscores).

CREATE TABLE IF NOT EXISTS public.nba_historical_closing_odds (
  id                      BIGSERIAL PRIMARY KEY,

  -- Game identity (matches nba_game_results on (game_date, home, away))
  game_date               DATE NOT NULL,
  away_team               TEXT NOT NULL,
  home_team               TEXT NOT NULL,

  -- Moneyline (american odds)
  close_home_ml           INT,
  close_away_ml           INT,

  -- Spread: line = close_spread (e.g. -6.5 = home favored by 6.5); odds
  -- for the point spread market. Uses standard basketball convention
  -- (negative = home favored) matching MLB/NCAAF, opposite of NFL.
  close_spread            NUMERIC,
  spread_home_odds        INT,
  spread_away_odds        INT,

  -- Total: line = close_total (e.g. 224.5); odds = over_odds / under_odds
  close_total             NUMERIC,
  over_odds               INT,
  under_odds              INT,

  -- Provenance
  source                  TEXT NOT NULL DEFAULT 'the_odds_api',
  raw_payload             JSONB,
  fetched_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (game_date, away_team, home_team)
);

CREATE INDEX IF NOT EXISTS nba_hist_odds_date_idx
  ON public.nba_historical_closing_odds (game_date DESC);
CREATE INDEX IF NOT EXISTS nba_hist_odds_teams_idx
  ON public.nba_historical_closing_odds (home_team, away_team);
CREATE INDEX IF NOT EXISTS nba_hist_odds_source_idx
  ON public.nba_historical_closing_odds (source);

ALTER TABLE public.nba_historical_closing_odds ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.nba_historical_closing_odds;
CREATE POLICY public_read
  ON public.nba_historical_closing_odds
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS service_role_write ON public.nba_historical_closing_odds;
CREATE POLICY service_role_write
  ON public.nba_historical_closing_odds
  FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE public.nba_historical_closing_odds IS
  'Historical NBA closing odds destination. Populated by nba_historical_odds_backfill.py '
  'via The Odds API historical endpoint; hydrates nba_game_results.close_* fields to '
  'unblock NBA LR training + signal registry validation.';

NOTIFY pgrst, 'reload schema';
