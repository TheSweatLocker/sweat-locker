-- 2026-08-20: sharp_source_calibration + sharp_agreement_calibration
-- The moat table pair for the per-source (FR/CZ/OC) rolling hit-rate study
-- (see project_per_source_tracker_moat_818). Populated nightly by
-- audit_sharp_source_calibration.py after finals grade. App reads these
-- for the "The Split" tab's rolling source track record + agreement badge.
--
-- sharp_source_calibration: per-source hit rate by (sport, source, market, window)
-- sharp_agreement_calibration: per-agreement-bucket hit rate (3/3 agree, dissent-OC, etc)

BEGIN;

CREATE TABLE IF NOT EXISTS sharp_source_calibration (
  id BIGSERIAL PRIMARY KEY,
  sport TEXT NOT NULL,
  source TEXT NOT NULL,             -- 'FR' | 'CZ' | 'OC'
  market TEXT NOT NULL,             -- 'ml' | 'rl' | 'total' | 'ALL'
  window_label TEXT NOT NULL,       -- '7d' | '30d' | 'lifetime'
  wins INT NOT NULL DEFAULT 0,
  losses INT NOT NULL DEFAULT 0,
  pushes INT NOT NULL DEFAULT 0,
  hit_rate NUMERIC(5,2),            -- % (wins / (wins+losses))
  sample_n INT NOT NULL DEFAULT 0,  -- wins + losses (excludes pushes)
  edge_pp NUMERIC(5,2),             -- hit_rate - 52.4 (breakeven at -110)
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT sharp_source_calibration_uk UNIQUE (sport, source, market, window_label)
);

CREATE INDEX IF NOT EXISTS sharp_source_calibration_lookup_idx
  ON sharp_source_calibration (sport, window_label);

CREATE TABLE IF NOT EXISTS sharp_agreement_calibration (
  id BIGSERIAL PRIMARY KEY,
  sport TEXT NOT NULL,
  bucket TEXT NOT NULL,             -- '3_of_3_AGREE' | '2_of_3_AGREE' | 'DISSENT_OC' | 'DISSENT_FR' | 'DISSENT_CZ' | 'MAJ_when_OC_dissents' | 'MAJ_when_FR_dissents' | 'MAJ_when_CZ_dissents'
  market TEXT NOT NULL,             -- 'ml' | 'rl' | 'total' | 'ALL'
  window_label TEXT NOT NULL,       -- '7d' | '30d' | 'lifetime'
  wins INT NOT NULL DEFAULT 0,
  losses INT NOT NULL DEFAULT 0,
  pushes INT NOT NULL DEFAULT 0,
  hit_rate NUMERIC(5,2),
  sample_n INT NOT NULL DEFAULT 0,
  edge_pp NUMERIC(5,2),
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT sharp_agreement_calibration_uk UNIQUE (sport, bucket, market, window_label)
);

CREATE INDEX IF NOT EXISTS sharp_agreement_calibration_lookup_idx
  ON sharp_agreement_calibration (sport, window_label);

ALTER TABLE sharp_source_calibration ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ssc_public_read ON sharp_source_calibration;
CREATE POLICY ssc_public_read ON sharp_source_calibration
  FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS ssc_service_write ON sharp_source_calibration;
CREATE POLICY ssc_service_write ON sharp_source_calibration
  FOR ALL TO service_role USING (true) WITH CHECK (true);

ALTER TABLE sharp_agreement_calibration ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sac_public_read ON sharp_agreement_calibration;
CREATE POLICY sac_public_read ON sharp_agreement_calibration
  FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS sac_service_write ON sharp_agreement_calibration;
CREATE POLICY sac_service_write ON sharp_agreement_calibration
  FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMIT;

NOTIFY pgrst, 'reload schema';
