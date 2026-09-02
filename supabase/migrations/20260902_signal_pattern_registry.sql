-- signal_pattern_registry (2026-09-02) — The Split rolling pattern hit rates
--
-- Powers the Split surface's "does this pattern hit?" data-backed rendering.
-- Populated by mlb_pipeline/compute_signal_patterns.py nightly per sport.
-- App reads on Split card render to show inline hit% context on each
-- line-movement flag ("SHARP TRIPLE + total: 68% n=45 L30d").
--
-- Distinct from sport_pattern_registry (Vault Match). This one tracks
-- SIGNAL patterns (from line_movement_flags classifications × market)
-- vs Vault Match patterns (game-level PATTERN_CATALOG entries with
-- custom matches_fn / outcome_fn).
--
-- Data contract:
--   sport         'MLB' | 'NFL' | 'NCAAF' | 'NBA' | 'NCAAB' | 'NHL'
--   pattern_key   classification value from line_movement_flags
--                 (e.g. 'SHARP_MOVE_TRIPLE_CONFIRMED', 'PUBLIC_MOVE_CONFIRMED')
--   market        'ml' | 'rl' | 'total' | 'ALL'  (ALL = combined across markets)
--   pattern_label user-friendly display string
--   description   plain-english "when this pattern fires…"
--   lookback_days rolling window (default 30d)
--   n_wins/losses/pushes  rolling W-L-P over lookback
--   hit_pct       n_wins / (n_wins + n_losses)  %
--   last_computed_at
--
-- Set-and-forget contract: cron writes; app reads. RLS public read.

CREATE TABLE IF NOT EXISTS public.signal_pattern_registry (
  sport                TEXT NOT NULL,
  pattern_key          TEXT NOT NULL,
  market               TEXT NOT NULL DEFAULT 'ALL',
  pattern_label        TEXT NOT NULL,
  description          TEXT,
  lookback_days        INT NOT NULL DEFAULT 30,
  n_wins               INT NOT NULL DEFAULT 0,
  n_losses             INT NOT NULL DEFAULT 0,
  n_pushes             INT NOT NULL DEFAULT 0,
  n_total              INT NOT NULL DEFAULT 0,
  hit_pct              NUMERIC(5,2),
  last_computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (sport, pattern_key, market)
);

COMMENT ON TABLE public.signal_pattern_registry IS
  '2026-09-02 The Split pattern rolling hit rates. Populated by compute_signal_patterns.py nightly; consumed by app for inline "X% hits when pattern fires" chip context.';

CREATE INDEX IF NOT EXISTS ix_signal_pattern_registry_sport
  ON public.signal_pattern_registry (sport);

ALTER TABLE public.signal_pattern_registry ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "signal_pattern_registry_read_public"
  ON public.signal_pattern_registry;
CREATE POLICY "signal_pattern_registry_read_public"
  ON public.signal_pattern_registry
  FOR SELECT
  USING (true);

GRANT SELECT ON public.signal_pattern_registry TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
