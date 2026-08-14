-- Line history (2026-08-13).
--
-- Powers the Steam Room tab's Line Movement view: a rolling snapshot of
-- odds per game/market/book/side so the app can render sparklines and
-- auto-detect steam moves + reverse line movement (RLM).
--
-- Data flow:
--   1. Existing odds cron writes to games/bookmakers cache each cycle
--   2. New line_history_snapshot.py runs after the odds pull, INSERTs one
--      row per (game_id, sport, market, book, side, ts) — never updates
--   3. App reads last 14 days for a given game, plots the drift
--   4. detect_steam_moves() job auto-flags multi-book coordinated shifts
--
-- Why rows, not JSON blobs: append-only makes retention trivial (drop
-- rows older than 14d nightly), and downstream aggregation (average
-- movement per hour, RLM detection) works with straightforward WHERE
-- filters rather than JSON path queries.
--
-- Sport universal — market column carries the sport's market type
-- (mlb: 'ml'/'spread'/'total'; nfl: same; ufc: 'ml' only). Book
-- column matches Odds API bookmaker keys ('draftkings', 'fanduel',
-- 'hardrockbet', etc).

CREATE TABLE IF NOT EXISTS public.line_history (
  id           BIGSERIAL PRIMARY KEY,
  sport        TEXT NOT NULL,
  game_id      TEXT NOT NULL,
  matchup      TEXT,                    -- "Away @ Home" for display convenience
  commence_time TIMESTAMPTZ,            -- game start; enables filtering to today/upcoming
  market       TEXT NOT NULL,           -- 'ml' | 'spread' | 'total'
  book         TEXT NOT NULL,           -- Odds API bookmaker key
  side         TEXT NOT NULL,           -- 'home'/'away' for ml/spread; 'over'/'under' for total
  line         NUMERIC,                 -- point value: spread number, total number; NULL for ml
  price        INT NOT NULL,            -- American odds
  captured_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Query pattern: give me the drift for game X market Y over last N days.
-- The (game_id, market, book, side, captured_at) index serves the sparkline
-- read directly. Time-range indexed for retention prune.
CREATE INDEX IF NOT EXISTS line_history_game_market_book_side_idx
  ON public.line_history (game_id, market, book, side, captured_at DESC);

CREATE INDEX IF NOT EXISTS line_history_captured_at_idx
  ON public.line_history (captured_at);

-- 2026-08-13: dropped WHERE commence_time > NOW() predicate. Postgres rejects
-- NOW() in a partial-index predicate (must be IMMUTABLE, NOW() is STABLE).
-- The unpartitioned index still serves the "upcoming games for this sport"
-- query fine — 14-day retention keeps the table small enough that a full
-- (sport, commence_time DESC) scan is cheap.
CREATE INDEX IF NOT EXISTS line_history_sport_upcoming_idx
  ON public.line_history (sport, commence_time DESC);

-- ─── Steam move / RLM detection ────────────────────────────────────────
--
-- Persist the detected patterns so the app can render badges without
-- recomputing on every render + the pipeline can audit accuracy over time.
--
-- pattern:
--   'steam'    → 2+ books shifted same direction within 15 min (sharp move)
--   'rlm'      → line moved opposite to majority bets% (public getting faded)
--   'limit'    → line moved with money% but NOT with bets% (money > bets, whale)

CREATE TABLE IF NOT EXISTS public.line_movement_flags (
  id             BIGSERIAL PRIMARY KEY,
  sport          TEXT NOT NULL,
  game_id        TEXT NOT NULL,
  market         TEXT NOT NULL,
  side           TEXT NOT NULL,           -- side benefitting from the move
  pattern        TEXT NOT NULL,           -- 'steam' | 'rlm' | 'limit'
  detail         TEXT,                    -- human-readable evidence line
  first_seen_at  TIMESTAMPTZ NOT NULL,
  last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (game_id, market, side, pattern)
);

CREATE INDEX IF NOT EXISTS line_movement_flags_game_idx
  ON public.line_movement_flags (game_id, last_seen_at DESC);

-- RLS: read-only for anon; write for pipeline (same policy as sibling tables)
ALTER TABLE public.line_history           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.line_movement_flags    ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_read ON public.line_history;
CREATE POLICY public_read ON public.line_history
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.line_history;
CREATE POLICY public_write ON public.line_history
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS public_read ON public.line_movement_flags;
CREATE POLICY public_read ON public.line_movement_flags
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.line_movement_flags;
CREATE POLICY public_write ON public.line_movement_flags
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

-- Retention: 14 days is enough for actionable line-move context. Older
-- snapshots become historical noise and inflate table size. A nightly
-- cron will run: DELETE FROM line_history WHERE captured_at < NOW() - INTERVAL '14 days';
-- (deferred; not needed until table is populated).

NOTIFY pgrst, 'reload schema';
