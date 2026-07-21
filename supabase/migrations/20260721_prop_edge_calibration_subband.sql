-- 2026-07-21: Sub-band conviction column for prop_edge_calibration
-- ============================================================
-- Per project_ha_under_conviction_band_720 audit: PRIME 85+ conviction band
-- hits 66.7% (n=21) while PRIME 75-84 hits 45.8% (n=24) — same tier badge,
-- opposite EV. Averaging all PRIME into one hit_rate hides this and lets
-- traps like PRIME 82 publish alongside real edges like PRIME 90+.
--
-- This migration adds a conviction_band column with values:
--   '85+', '75-84', '65-74', '<65', 'ALL' (backward-compat aggregate row)
--
-- prop_edge_calibrator.py update (same commit) will write BOTH the legacy
-- ALL-row (for backward compat) AND new sub-band rows so downstream consumers
-- can pick the granularity they need.
--
-- Idempotent (ADD COLUMN IF NOT EXISTS + DROP/CREATE unique constraint).

ALTER TABLE public.prop_edge_calibration
  ADD COLUMN IF NOT EXISTS conviction_band text DEFAULT 'ALL';

-- Any existing rows are aggregates — mark them
UPDATE public.prop_edge_calibration
  SET conviction_band = 'ALL'
  WHERE conviction_band IS NULL;

-- Drop old unique constraint if it exists (was likely on tier+prop_type+direction+window_days)
DO $$
DECLARE
  cons_name text;
BEGIN
  SELECT conname INTO cons_name
  FROM pg_constraint
  WHERE conrelid = 'public.prop_edge_calibration'::regclass
    AND contype = 'u'
  LIMIT 1;
  IF cons_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.prop_edge_calibration DROP CONSTRAINT %I', cons_name);
    RAISE NOTICE 'Dropped old unique constraint: %', cons_name;
  END IF;
END $$;

-- New unique constraint including conviction_band
ALTER TABLE public.prop_edge_calibration
  ADD CONSTRAINT prop_edge_calibration_bucket_key
  UNIQUE (tier, prop_type, direction, conviction_band, window_days, computed_at);

-- Index for lookup by (prop_type, tier, conviction_band) — most common query
CREATE INDEX IF NOT EXISTS idx_prop_edge_calibration_lookup
  ON public.prop_edge_calibration (prop_type, tier, conviction_band)
  WHERE computed_at = (SELECT MAX(computed_at) FROM prop_edge_calibration);

-- Verify
SELECT
  count(*) FILTER (WHERE conviction_band = 'ALL') AS all_rows,
  count(*) FILTER (WHERE conviction_band <> 'ALL') AS sub_band_rows,
  count(*) AS total_rows
FROM public.prop_edge_calibration;

NOTIFY pgrst, 'reload schema';
