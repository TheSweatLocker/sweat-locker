-- UFC fighter fight-by-fight history
-- ============================================================
-- Captures every fight from each fighter's UFCStats career page.
-- Used to compute pre-fight snapshots for v1 model training so we
-- avoid forward-looking bias (e.g. using 25-fight career stats to
-- predict their 5th fight).
--
-- For any training row in ufc_fight_results, we can derive pre-fight
-- features by querying this table for fight_date < target_fight_date.
--
-- Apply via Supabase SQL editor.

CREATE TABLE IF NOT EXISTS ufc_fighter_history (
  id BIGSERIAL PRIMARY KEY,
  fighter_url TEXT NOT NULL,
  fighter_name TEXT NOT NULL,

  -- Fight metadata
  fight_date DATE,
  event_name TEXT,
  opponent_name TEXT,
  opponent_url TEXT,

  -- Result for this fighter
  result TEXT CHECK (result IN ('win', 'loss', 'draw', 'no_contest', 'next')),
  method TEXT,           -- 'KO/TKO', 'SUB', 'U-DEC', 'S-DEC', 'M-DEC', 'DQ'
  round INT,
  time TEXT,

  -- Per-fight stats (when available from fight detail page)
  sig_strikes_landed INT,
  sig_strikes_attempted INT,
  takedowns_landed INT,
  takedowns_attempted INT,

  scraped_at TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE (fighter_url, fight_date, opponent_url)
);

CREATE INDEX IF NOT EXISTS idx_ufc_fighter_history_url_date
  ON ufc_fighter_history(fighter_url, fight_date DESC);
CREATE INDEX IF NOT EXISTS idx_ufc_fighter_history_date
  ON ufc_fighter_history(fight_date DESC);
