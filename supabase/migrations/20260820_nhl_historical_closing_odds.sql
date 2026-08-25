-- NHL historical closing odds (2026-08-20).
--
-- Destination table for backfilling historical NHL closing lines against
-- the 1,335 games in nhl_game_results. Enables validation of the 35 NHL
-- signals shipped 2026-08-19 (most are UNVALIDATED because there's no
-- closing-line data to grade them against).
--
-- Shape mirrors ufc_historical_closing_odds (2026-08-19) — one row per
-- historical game with moneyline, puckline (line + odds), and total (line
-- + over/under odds). `source` tag identifies the scrape origin
-- (hockey_reference / oddsshark / kaggle / etc.) so multiple sources can
-- coexist and the grader can prefer the most trustworthy per game.
--
-- Key: game_url when we have it (hockey-reference boxscore URL); the
-- (game_date, away_team, home_team) triple fallback exists as a second
-- unique constraint so upserts work even when game_url is NULL (e.g.
-- Kaggle CSV imports without source URLs).

CREATE TABLE IF NOT EXISTS public.nhl_historical_closing_odds (
  id                      BIGSERIAL PRIMARY KEY,

  -- Game identity (matches nhl_game_results on (game_date, home, away))
  game_date               DATE NOT NULL,
  away_team               TEXT NOT NULL,
  home_team               TEXT NOT NULL,
  game_url                TEXT,                 -- e.g. hockey-reference boxscore URL

  -- Moneyline (american odds)
  close_home_ml           INT,
  close_away_ml           INT,

  -- Puckline: line = puckline_home / _away (numeric, e.g. -1.5 / +1.5)
  -- odds   = close_puckline_home / _away (american int)
  puckline_home           NUMERIC,
  puckline_away           NUMERIC,
  close_puckline_home     INT,
  close_puckline_away     INT,

  -- Total: line = close_total (e.g. 6.5); odds = over_odds / under_odds
  close_total             NUMERIC,
  over_odds               INT,
  under_odds              INT,

  -- Provenance
  source                  TEXT NOT NULL DEFAULT 'hockey_reference',
  raw_payload             JSONB,               -- optional full-source dump for audit
  fetched_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Uniqueness: prefer game_url when present, else (date, teams).
  -- Two separate unique constraints keep both upsert paths open.
  UNIQUE (game_url),
  UNIQUE (game_date, away_team, home_team)
);

CREATE INDEX IF NOT EXISTS nhl_hist_odds_date_idx
  ON public.nhl_historical_closing_odds (game_date DESC);
CREATE INDEX IF NOT EXISTS nhl_hist_odds_teams_idx
  ON public.nhl_historical_closing_odds (home_team, away_team);
CREATE INDEX IF NOT EXISTS nhl_hist_odds_source_idx
  ON public.nhl_historical_closing_odds (source);

-- RLS parity with the rest of the nhl_* tables (tightened 2026-08-17)
ALTER TABLE public.nhl_historical_closing_odds ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.nhl_historical_closing_odds;
CREATE POLICY public_read
  ON public.nhl_historical_closing_odds
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS service_role_write ON public.nhl_historical_closing_odds;
CREATE POLICY service_role_write
  ON public.nhl_historical_closing_odds
  FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE public.nhl_historical_closing_odds IS
  'Historical NHL closing odds destination table. Multi-source (see `source` column). '
  'Populated by _nhl_*_odds_backfill_*.py scripts; enables signal_registry re-grading '
  'of the 1,335 games in nhl_game_results before Oct 7 season opener.';

-- Force PostgREST to reload its schema cache so writes don't 400 with PGRST204
NOTIFY pgrst, 'reload schema';
