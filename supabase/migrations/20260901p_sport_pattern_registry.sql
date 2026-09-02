-- sport_pattern_registry (2026-09-01) — Vault Match backbone
--
-- Proprietary system-detected patterns: "external X record + model Y
-- call = 74% n=23 last 30d" and similar cross-signal edges surfaced
-- as a game card badge (Vault Match). Each row is one pattern's
-- rolling metric snapshot; the actual matching logic (does THIS game
-- fire THIS pattern?) lives in Python (mlb_pipeline/compute_sport_patterns.py)
-- so pattern definitions can evolve without schema churn.
--
-- Design contract:
-- - This table is the persisted STATS side (n_wins/n_losses/hit_pct).
-- - Python PATTERN_CATALOG is the DEFINITIONS side (criteria per pattern).
-- - Nightly cron runs compute_sport_patterns.py which iterates the
--   catalog, joins external_picks × game_context × _game_results,
--   and upserts one row per (sport, pattern_key).
-- - Context builder later reads this table + runs the criteria against
--   today's game rows → attaches matched_patterns[] to ctx JSONB.
-- - Card renders 🎯 Vault Match badge when any matched pattern has
--   hit_pct >= 65 AND n_total >= 15.
--
-- Threshold rationale: hit_pct 65% and n_total 15 balance edge
-- strength (65% is +30% ROI at -110) with sample noise (n=15 gives
-- ~13pp standard error, so a 65% pattern is meaningfully >50 at 2σ).
-- Patterns below threshold stay in the registry but don't fire the
-- badge — we still track them for calibration drift.

CREATE TABLE IF NOT EXISTS public.sport_pattern_registry (
  sport                TEXT NOT NULL,
  pattern_key          TEXT NOT NULL,
  pattern_label        TEXT NOT NULL,
  pattern_description  TEXT,
  lookback_days        INT NOT NULL DEFAULT 30,
  n_wins               INT NOT NULL DEFAULT 0,
  n_losses             INT NOT NULL DEFAULT 0,
  n_pushes             INT NOT NULL DEFAULT 0,
  n_total              INT NOT NULL DEFAULT 0,
  hit_pct              NUMERIC(5,2),
  last_computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (sport, pattern_key)
);

COMMENT ON TABLE public.sport_pattern_registry IS
  '2026-09-01 Vault Match backbone. Rolling per-pattern hit rates from graded external_picks × game_context × _game_results joins. Populated by mlb_pipeline/compute_sport_patterns.py; consumed by context builder to attach matched_patterns[] to game ctx.';

-- Index for pattern lookup by sport (context builder scans all patterns
-- per sport when checking today's games).
CREATE INDEX IF NOT EXISTS ix_sport_pattern_registry_sport
  ON public.sport_pattern_registry (sport);

-- RLS: read-only for anon/authenticated. Only service role writes.
ALTER TABLE public.sport_pattern_registry ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "sport_pattern_registry_read_public" ON public.sport_pattern_registry;
CREATE POLICY "sport_pattern_registry_read_public"
  ON public.sport_pattern_registry
  FOR SELECT
  USING (true);

GRANT SELECT ON public.sport_pattern_registry TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
