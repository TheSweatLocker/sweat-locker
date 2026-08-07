-- book_lines: sport-universal per-book line history (2026-08-07)
--
-- Foundation for sharp/public divergence detection. mlb_line_history
-- collapses all bookmakers into a median per poll, discarding per-book
-- data. This table preserves one row per (sport, game, book, market)
-- when the book's line CHANGES vs its most recent stored value.
--
-- Change-only writes keep volume manageable: ~15 games × 14 books × 3
-- markets × 48 polls/day = 30k potential rows/day, but if a book holds
-- steady only the first + change rows persist — typical volume closer
-- to a few thousand rows/day for MLB.
--
-- Sport-universal by design: NFL/NCAAF/NCAAB/NBA/UFC pollers all write
-- to the same table with their own sport tag. Divergence detector
-- reads the same schema across every sport.

CREATE TABLE IF NOT EXISTS public.book_lines (
  id           BIGSERIAL PRIMARY KEY,
  sport        TEXT NOT NULL,             -- 'MLB' | 'NFL' | 'NCAAF' | ...
  game_id      TEXT NOT NULL,
  game_date    DATE NOT NULL,
  book_key     TEXT NOT NULL,             -- Odds API bookmaker key (lowercase)
  book_title   TEXT NOT NULL,             -- human-readable book name
  book_tier    TEXT NOT NULL,             -- 'sharp' | 'mid' | 'public'
  market       TEXT NOT NULL,             -- 'total' | 'spread' | 'ml'
  line         NUMERIC,                   -- total_line or spread; null for ML
  home_odds    NUMERIC,                   -- over_odds for total, spread_home_odds for spread, home_ml for ml
  away_odds    NUMERIC,                   -- under_odds for total, spread_away_odds for spread, away_ml for ml
  fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Divergence detector reads recent history per (sport, game, market) —
-- this index makes it O(log n).
CREATE INDEX IF NOT EXISTS book_lines_game_market_idx
  ON public.book_lines(sport, game_id, market, fetched_at DESC);

-- Backtest/audit queries filter by date range + tier
CREATE INDEX IF NOT EXISTS book_lines_date_tier_idx
  ON public.book_lines(game_date, book_tier);

-- Reader access via PostgREST
ALTER TABLE public.book_lines ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS book_lines_read ON public.book_lines;
CREATE POLICY book_lines_read ON public.book_lines
  FOR SELECT USING (true);

-- Service-role write (pipeline uses SUPABASE_KEY which is service_role)
DROP POLICY IF EXISTS book_lines_write ON public.book_lines;
CREATE POLICY book_lines_write ON public.book_lines
  FOR INSERT WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
