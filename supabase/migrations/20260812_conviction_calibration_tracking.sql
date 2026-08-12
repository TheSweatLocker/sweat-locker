-- Conviction calibration tracking (2026-08-12).
--
-- Tracks every conviction adjustment made by conviction_calibration_pass
-- so we can measure whether each RULE is actually calibrated. Without
-- this, rules like MULTI_SIGNAL_PROMO (force conv to 78 when confluence+
-- MC+refit align) are pure theory — we have no data on whether promoted
-- picks actually hit at the higher tier.
--
-- Rules tracked (extensible — add rows as new sources land):
--   MULTI_SIGNAL_PROMO       — game read promoted to conv 78+
--   HOLE_60_64_CAP           — total UNDER capped from 60-64 to 55
--   HOLE_60_64_BOOST         — total UNDER boosted from 60-64 to 65
--   KS_UNDER_LOW_CONV        — ks_under BACK below conv 60 forced PASS
--
-- Populated by:
--   conviction_calibration_pass.py (main writer)
--
-- Graded by:
--   Nightly resolver — reads prop_jerry_reads / jerry_reads result
--   and stamps hit=true/false + resolved_at.
--
-- Consumed by:
--   Weekly calibration report to decide whether to keep / tune / kill
--   each rule.

CREATE TABLE IF NOT EXISTS conviction_calibration_events (
  id SERIAL PRIMARY KEY,
  game_date DATE NOT NULL,
  sport TEXT NOT NULL DEFAULT 'MLB',
  source_table TEXT NOT NULL,          -- 'jerry_reads' or 'prop_jerry_reads'
  source_id INT NOT NULL,              -- ID in source table
  rule TEXT NOT NULL,                  -- MULTI_SIGNAL_PROMO / HOLE_60_64_CAP / etc
  original_conviction INT,             -- before adjustment
  new_conviction INT,                  -- after adjustment
  original_verdict TEXT,               -- for prop rules that change verdict
  new_verdict TEXT,
  note TEXT,                           -- human-readable reasoning
  hit BOOLEAN,                         -- Win = TRUE, Loss = FALSE, NULL = pending
  resolved_at TIMESTAMPTZ,             -- when resolver graded
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (game_date, source_table, source_id, rule)
);

CREATE INDEX IF NOT EXISTS idx_cce_rule_date ON conviction_calibration_events
  (rule, game_date DESC);
CREATE INDEX IF NOT EXISTS idx_cce_source ON conviction_calibration_events
  (source_table, source_id);
CREATE INDEX IF NOT EXISTS idx_cce_unresolved ON conviction_calibration_events
  (game_date, resolved_at) WHERE resolved_at IS NULL;

NOTIFY pgrst, 'reload schema';
