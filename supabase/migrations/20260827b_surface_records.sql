-- surface_records: single source of truth for per-surface per-sport per-window
-- W/L/P + units_net. Both the Sharp Card and the Receipts tab read from this,
-- so the numbers can never disagree.
--
-- Rows are (sport, surface, window) tuples. The aggregator
-- compute_surface_records.py rebuilds this table daily and upserts every
-- combination — sports that lack data yield zero rows (skipped, not stubbed).

CREATE TABLE IF NOT EXISTS surface_records (
  sport            TEXT        NOT NULL,   -- MLB / NFL / NCAAF / UFC / NBA / NHL / NCAAB / ALL
  surface          TEXT        NOT NULL,   -- sharp / prop / ladder / ledger / potd
  window           TEXT        NOT NULL,   -- mtd / d7 / d30 / lifetime
  wins             INT         NOT NULL DEFAULT 0,
  losses           INT         NOT NULL DEFAULT 0,
  pushes           INT         NOT NULL DEFAULT 0,
  units_net        NUMERIC(10,2) NOT NULL DEFAULT 0,
  picks_count      INT         NOT NULL DEFAULT 0,
  hit_rate         NUMERIC(5,3),
  roi_pct          NUMERIC(6,2),
  epoch_start      DATE,                    -- earliest date included
  last_pick_date   DATE,                    -- most recent graded pick
  last_computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (sport, surface, window)
);

CREATE INDEX IF NOT EXISTS idx_surface_records_lookup
  ON surface_records (surface, sport, window);

ALTER TABLE surface_records ENABLE ROW LEVEL SECURITY;

-- Public read
DROP POLICY IF EXISTS surface_records_read ON surface_records;
CREATE POLICY surface_records_read ON surface_records
  FOR SELECT USING (true);

-- Service-role write only
DROP POLICY IF EXISTS surface_records_write ON surface_records;
CREATE POLICY surface_records_write ON surface_records
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

NOTIFY pgrst, 'reload schema';
