-- UFC fight results table for training data + audit calibration
-- ============================================================
-- Captures resolved fight outcomes (winner, method, round, distance)
-- so we can:
--   1. Train the proprietary UFC model (target: July 4 White House card)
--   2. Audit prop cohorts the same way we do for MLB:
--        ufc_fight_total_rounds_over_2_5
--        ufc_fight_method_ko_tko
--        ufc_fight_went_distance
--   3. Build per-fighter NRFI-equivalents (e.g., decision rate, finishing rate)
--
-- Apply via Supabase SQL editor.

CREATE TABLE IF NOT EXISTS ufc_fight_results (
  id BIGSERIAL PRIMARY KEY,

  -- Event metadata
  event_name TEXT NOT NULL,
  event_date DATE NOT NULL,
  event_url TEXT,
  fight_order INT,                       -- 1 = main event, 2 = co-main, etc

  -- Fighter identifiers
  fighter_a TEXT NOT NULL,
  fighter_b TEXT NOT NULL,
  fighter_a_url TEXT,
  fighter_b_url TEXT,
  weight_class TEXT,

  -- Outcome
  winner TEXT CHECK (winner IN ('a', 'b', 'draw', 'no_contest', 'cancelled')),
  method TEXT,                            -- 'KO/TKO', 'SUB', 'U-DEC', 'S-DEC', 'M-DEC', 'DQ'
  method_detail TEXT,                     -- e.g. "Punches", "Rear Naked Choke"
  round INT,                              -- 1-5 (or null for cancelled)
  time TEXT,                              -- "mm:ss"
  total_rounds_scheduled INT DEFAULT 3,   -- 3 (regular) or 5 (main events / title fights)
  went_distance BOOLEAN,                  -- true if went to scorecards

  -- Pre-fight snapshot (for training v1 model)
  fighter_a_record_pre TEXT,              -- "15-0-0"
  fighter_b_record_pre TEXT,
  fighter_a_finishing_rate NUMERIC,       -- pre-fight career
  fighter_b_finishing_rate NUMERIC,

  resolved_at TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE (event_name, fighter_a, fighter_b)
);

CREATE INDEX IF NOT EXISTS idx_ufc_fight_results_event_date
  ON ufc_fight_results(event_date DESC);
CREATE INDEX IF NOT EXISTS idx_ufc_fight_results_method
  ON ufc_fight_results(method);
