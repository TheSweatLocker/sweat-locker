-- 2026-07-22 — External source rolling calibration (transparency step 2).
--
-- Mirrors mlb_tier_calibration shape but for external picks: per
-- (source, sport, surface, fade_flag) rolling 7d/30d/lifetime hit rates.
-- Written nightly by audit_external_source_calibration.py after
-- resolve_externals.py stamps results. Drives:
--   - Dynamic fade_flag refresh (replace 7/20 hardcoded audit tags)
--   - Consensus_fade_alert detector inputs
--   - App-side per-source track records (Tier 2 UX)

CREATE TABLE IF NOT EXISTS external_source_calibration (
  cal_id         UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  source         TEXT NOT NULL,           -- 'covers', 'dimers', ...
  sport          TEXT NOT NULL,           -- 'MLB' | 'NFL' | 'NCAAB'
  surface        TEXT NOT NULL,           -- 'ml' | 'spread' | 'rl' | 'total'
  window_label   TEXT NOT NULL,           -- '7d' | '30d' | 'lifetime'
  wins           INT NOT NULL DEFAULT 0,
  losses         INT NOT NULL DEFAULT 0,
  pushes         INT NOT NULL DEFAULT 0,
  hit_pct        NUMERIC,
  sample_n       INT NOT NULL DEFAULT 0,
  -- Broken out by our fade_flag tag for tag-level performance
  boost_wins     INT DEFAULT 0,
  boost_losses   INT DEFAULT 0,
  fade_wins      INT DEFAULT 0,
  fade_losses    INT DEFAULT 0,
  trust_wins     INT DEFAULT 0,
  trust_losses   INT DEFAULT 0,
  neutral_wins   INT DEFAULT 0,
  neutral_losses INT DEFAULT 0,
  computed_at    TIMESTAMPTZ DEFAULT NOW(),
  computed_date  DATE NOT NULL DEFAULT CURRENT_DATE,
  notes          TEXT,
  UNIQUE (source, sport, surface, window_label, computed_date)
);

CREATE INDEX IF NOT EXISTS idx_external_source_cal_window
  ON external_source_calibration (window_label, sport, computed_date DESC);

CREATE INDEX IF NOT EXISTS idx_external_source_cal_hit
  ON external_source_calibration (hit_pct DESC) WHERE hit_pct IS NOT NULL;

ALTER TABLE external_source_calibration ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "external_source_cal select all" ON external_source_calibration;
CREATE POLICY "external_source_cal select all"
  ON external_source_calibration FOR SELECT USING (true);

DROP POLICY IF EXISTS "external_source_cal write anon" ON external_source_calibration;
CREATE POLICY "external_source_cal write anon"
  ON external_source_calibration FOR ALL USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
