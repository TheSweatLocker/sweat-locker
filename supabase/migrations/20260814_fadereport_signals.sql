-- Fadereport signals table (2026-08-14).
--
-- Nightly scrape of fadereport.com per-sport pages. Fadereport is a
-- curated sharp-signal service — each entry represents ONE game × ONE
-- market where their algo detected sharp action (bets% vs money% split
-- >= their threshold, currently ~10pt lean / 20pt strong).
--
-- We store their signals as a SUPPLEMENTARY sharp lens alongside our
-- primary OddsCrowd splits (oddscrowd_snapshot on *_game_context).
-- When both agree tightly, high-confidence sharp signal. When they
-- disagree, book-mix noise — trust neither individually.
--
-- Real sharp arbiter is Pinnacle line movement — planned separate
-- capture. Once we have 4-6 weeks of accumulated data, we can validate
-- OC vs FR against Pinnacle to determine which aligns with actual
-- sharp market movement.
--
-- Sport-universal schema — one table serves MLB/NCAAB/NHL/NFL/NCAAF.
--
-- Uniqueness: (snapshot_date, sport, away_team, home_team, market).
-- FR may show multiple markets per game — each is a separate row.

CREATE TABLE IF NOT EXISTS public.fadereport_signals (
  id                BIGSERIAL PRIMARY KEY,

  snapshot_date     DATE NOT NULL,
  sport             TEXT NOT NULL,                    -- MLB / NCAAB / NHL / NFL / NCAAF
  game_id           TEXT,                             -- FK to *_game_context.game_id where resolvable
  away_team         TEXT NOT NULL,                    -- as displayed on fadereport
  home_team         TEXT NOT NULL,
  game_time_et      TEXT,                             -- "7:10 PM" as displayed (not parsed)

  market            TEXT NOT NULL,                    -- 'ml' | 'spread' | 'total'
  sharp_side_raw    TEXT,                             -- 'Cardinals' | 'OVER o7.5' | 'Rays -1.5' etc.
  sharp_side_norm   TEXT,                             -- 'home' | 'away' | 'over' | 'under'
  strength_pts      INTEGER,                          -- +46 = strong sharp, +13 = lean sharp
  strength_tier     TEXT,                             -- 'strong' (>=20) | 'lean' (10-19)

  -- Split breakdown (as shown on FR)
  bets_side_pct     INTEGER,                          -- % of bets on the SHARP side
  money_side_pct    INTEGER,                          -- % of money on the SHARP side
  bets_other_pct    INTEGER,
  money_other_pct   INTEGER,

  reasoning         TEXT,                             -- "Why the sharps like it" blurb
  raw_snapshot      JSONB,                            -- full raw dict for debug

  fetched_at        TIMESTAMPTZ DEFAULT NOW(),
  generated_at      TIMESTAMPTZ,

  UNIQUE (snapshot_date, sport, away_team, home_team, market)
);

CREATE INDEX IF NOT EXISTS fadereport_signals_lookup_idx
  ON public.fadereport_signals (snapshot_date DESC, sport, game_id);

CREATE INDEX IF NOT EXISTS fadereport_signals_strong_idx
  ON public.fadereport_signals (snapshot_date DESC, sport)
  WHERE strength_tier = 'strong';

NOTIFY pgrst, 'reload schema';
