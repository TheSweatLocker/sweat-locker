-- 2026-08-18: sweat_tier_config — server-side thresholds for the client
-- sweat-tier fallback formula. Kills the hardcoded 80/65/50 in
-- app/index.tsx line 2961-2964 so tier calibration changes don't
-- require an App Store resubmission.
--
-- Client reads on tab-load, caches for session, falls back to the
-- hardcoded defaults if fetch fails.

BEGIN;

CREATE TABLE IF NOT EXISTS sweat_tier_config (
  id INT PRIMARY KEY DEFAULT 1,
  prime_cutoff INT NOT NULL DEFAULT 80,
  strong_cutoff INT NOT NULL DEFAULT 65,
  light_cutoff INT NOT NULL DEFAULT 50,
  notes TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT sweat_tier_config_singleton CHECK (id = 1)
);

-- Seed the current defaults (matches app hardcode)
INSERT INTO sweat_tier_config (id, prime_cutoff, strong_cutoff, light_cutoff, notes)
VALUES (1, 80, 65, 50, 'Initial seed — matches app hardcode. Bump when calibration audit shifts.')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE sweat_tier_config ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sweat_tier_config_public_read ON sweat_tier_config;
CREATE POLICY sweat_tier_config_public_read
  ON sweat_tier_config FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS sweat_tier_config_service_role_write ON sweat_tier_config;
CREATE POLICY sweat_tier_config_service_role_write
  ON sweat_tier_config FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE sweat_tier_config IS
  'Singleton config for client sweat-tier fallback cutoffs. Kills 80/65/50 hardcode in app so recalibration ships without App Store resubmit.';

COMMIT;

NOTIFY pgrst, 'reload schema';
