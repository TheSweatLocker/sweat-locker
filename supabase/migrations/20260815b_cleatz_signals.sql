-- Cleatz public splits signals (2026-08-15 pm).
--
-- Third public-splits source (alongside OddsCrowd + Fadereport). Cleatz
-- publishes full-slate splits for every game across ML / Total / Run Line
-- with BOTH handle% and bets% per side. No editorial curation — every
-- game covered.
--
-- Fields mirror fadereport_signals shape for uniform downstream classifier
-- joins. Renamed sharp/other suffix to avoid ambiguity:
--   sharp_bets_pct   = bets% on the sharp side
--   sharp_handle_pct = handle% on the sharp side
--   other_bets_pct   = bets% on the opposite side
--   other_handle_pct = handle% on the opposite side
--   divergence       = sharp_handle_pct - sharp_bets_pct (positive = sharp lean)

CREATE TABLE IF NOT EXISTS public.cleatz_signals (
  id                BIGSERIAL PRIMARY KEY,
  snapshot_date     DATE NOT NULL,
  sport             TEXT NOT NULL,
  game_id           TEXT,
  away_team         TEXT NOT NULL,
  home_team         TEXT NOT NULL,
  market            TEXT NOT NULL,             -- 'ml' | 'rl' | 'total'
  sharp_side_raw    TEXT,                       -- 'BAL Orioles', 'Over 7.5', etc
  sharp_side_norm   TEXT,                       -- 'home' | 'away' | 'over' | 'under'
  sharp_bets_pct    INT,
  sharp_handle_pct  INT,
  other_bets_pct    INT,
  other_handle_pct  INT,
  divergence        INT,
  raw_snapshot      JSONB,
  fetched_at        TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE (snapshot_date, sport, away_team, home_team, market)
);

CREATE INDEX IF NOT EXISTS cleatz_signals_gid_idx
  ON public.cleatz_signals (game_id, market);
CREATE INDEX IF NOT EXISTS cleatz_signals_date_sport_idx
  ON public.cleatz_signals (snapshot_date DESC, sport);

COMMENT ON TABLE public.cleatz_signals IS
  'Cleatz.com public splits — 3rd source alongside OddsCrowd + Fadereport. Full-slate coverage.';

NOTIFY pgrst, 'reload schema';
