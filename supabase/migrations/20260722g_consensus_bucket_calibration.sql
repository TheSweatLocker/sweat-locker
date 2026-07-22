-- 2026-07-22 — Consensus-bucket calibration table.
--
-- Answers "when N% of books agree AND our model {aligns / disagrees},
-- what's the historical hit rate for that consensus side?" — the actual
-- audit that substantiates the consensus_fade detector.
--
-- Without this, the detector was firing based on ONE data point (53%
-- aggregate 7/21 n=13). This turns the detector from a hypothesis
-- into a data-driven signal.
--
-- Filled nightly by audit_consensus_bucket_calibration.py after
-- resolve_externals grades individual picks.

CREATE TABLE IF NOT EXISTS consensus_bucket_calibration (
  bucket_id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  sport                TEXT NOT NULL,               -- MLB / NFL / NCAAB
  surface              TEXT NOT NULL,               -- ml / spread / rl / total
  pct_band             TEXT NOT NULL,               -- '75-84' / '85-94' / '95-100'
  model_alignment      TEXT NOT NULL,               -- 'aligned' / 'contra' / 'unknown'
  window_label         TEXT NOT NULL,               -- '30d' / '60d' / 'lifetime'
  wins                 INT NOT NULL DEFAULT 0,       -- consensus side won
  losses               INT NOT NULL DEFAULT 0,       -- consensus side lost
  pushes               INT NOT NULL DEFAULT 0,
  hit_pct              NUMERIC,                      -- W / (W+L)
  sample_n             INT NOT NULL DEFAULT 0,
  -- Confidence classification — used by detector to decide flag vs monitor
  confidence           TEXT,                         -- 'high' (n>=50) / 'medium' (n>=20) / 'low' (n<20)
  computed_at          TIMESTAMPTZ DEFAULT NOW(),
  computed_date        DATE NOT NULL DEFAULT CURRENT_DATE,
  UNIQUE (sport, surface, pct_band, model_alignment, window_label, computed_date)
);

CREATE INDEX IF NOT EXISTS idx_consensus_bucket_lookup
  ON consensus_bucket_calibration
     (sport, surface, pct_band, model_alignment, window_label, computed_date DESC);

ALTER TABLE consensus_bucket_calibration ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "consensus_bucket_cal select all" ON consensus_bucket_calibration;
CREATE POLICY "consensus_bucket_cal select all"
  ON consensus_bucket_calibration FOR SELECT USING (true);

DROP POLICY IF EXISTS "consensus_bucket_cal write anon" ON consensus_bucket_calibration;
CREATE POLICY "consensus_bucket_cal write anon"
  ON consensus_bucket_calibration FOR ALL USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
