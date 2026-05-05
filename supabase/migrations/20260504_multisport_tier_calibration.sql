-- Multi-sport support for tier_calibration
-- ============================================================
-- The mlb_tier_calibration table holds rolling 7d/30d/std hit rates per
-- audited cohort (NRFI/YRFI bands, confluence tiers, spread_delta buckets).
-- It powers the auto-fade module + audit-weighted Daily Degen prioritization.
--
-- Currently MLB-only. Adding a `sport` column with default 'mlb' so the
-- same table can house cohorts for NBA / NFL / NCAAB / etc. without a
-- table-rename migration later. Code that reads/writes filters by sport.
--
-- Naming note: table stays mlb_tier_calibration for now to avoid breaking
-- the 8 Python files + audit cron that already reference it. When NBA's
-- pipeline is built we can either keep this name (technically a misnomer
-- for a multi-sport table) or do a coordinated rename then. Semantic
-- breakage is what matters; the column gives us the abstraction we need.
--
-- Apply via Supabase SQL editor.

ALTER TABLE mlb_tier_calibration
  ADD COLUMN IF NOT EXISTS sport TEXT NOT NULL DEFAULT 'mlb';

-- Index by (sport, tier, window_label) so per-sport reads stay fast.
-- Existing primary key / unique constraints on (tier, window_label,
-- computed_date) need to be replaced with sport-aware versions when
-- we onboard a second sport. Defer that until needed.
CREATE INDEX IF NOT EXISTS idx_tier_calibration_sport
  ON mlb_tier_calibration(sport, tier, window_label);
