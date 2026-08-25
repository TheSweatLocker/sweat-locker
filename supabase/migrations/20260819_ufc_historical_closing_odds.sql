-- UFC historical closing odds (2026-08-19).
--
-- BackFightOdds backfill destination — one row per historical fight
-- with per-book median + best closing odds. Enables ROI backtesting
-- of the UFC XGBoost tier logic against the 1,115-fight training set
-- (via mlb_pipeline/_ufc_bfo_backfill_2026-08-19.py).
--
-- Keyed by (event_url, fighter_a, fighter_b) matching the ufc_fight_results
-- row uniquely. Left-join in the backtest script to attach odds to each
-- graded fight.

CREATE TABLE IF NOT EXISTS public.ufc_historical_closing_odds (
  id                BIGSERIAL PRIMARY KEY,
  event_name        TEXT NOT NULL,
  event_date        DATE,
  event_url         TEXT NOT NULL,       -- ufcstats.com event URL (matches ufc_fight_results.event_url)
  fighter_a         TEXT NOT NULL,
  fighter_b         TEXT NOT NULL,
  fighter_a_url     TEXT,
  fighter_b_url     TEXT,
  odds_a_median     NUMERIC,             -- decimal odds median across books
  odds_b_median     NUMERIC,
  odds_a_best       NUMERIC,             -- best decimal (max) for side A
  odds_b_best       NUMERIC,
  odds_book_count   INT,                 -- how many books contributed
  bfo_event_url     TEXT,                -- BestFightOdds source page (audit)
  raw_books         JSONB,               -- per-book breakdown if the scraper preserved it
  fetched_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (event_url, fighter_a, fighter_b)
);

CREATE INDEX IF NOT EXISTS ufc_hist_odds_event_idx
  ON public.ufc_historical_closing_odds (event_url);
CREATE INDEX IF NOT EXISTS ufc_hist_odds_date_idx
  ON public.ufc_historical_closing_odds (event_date DESC);
CREATE INDEX IF NOT EXISTS ufc_hist_odds_fighters_idx
  ON public.ufc_historical_closing_odds (fighter_a, fighter_b);

COMMENT ON TABLE public.ufc_historical_closing_odds IS
  'BFO-scraped historical closing odds for the 1,115 fights in ufc_fight_results. '
  'Populated by _ufc_bfo_backfill_*.py; enables ROI backtest of UFC tier logic.';

NOTIFY pgrst, 'reload schema';
