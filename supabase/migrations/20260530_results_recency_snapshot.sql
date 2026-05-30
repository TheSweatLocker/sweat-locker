-- 2026-05-30 — Snapshot recency / hot-cold signals into mlb_game_results.
--
-- The Jerry Model's drift signal (offense_drift = L10 R/G - season R/G)
-- is the single strongest user-flagged factor for catching hot/cold bat
-- transitions. Backtest can only tune the recency weights when historical
-- drift data is preserved per game — currently mlb_game_results stores
-- only season-level offense (wrc_plus, ops, runs_per_game) so the audit
-- treats every game as drift=None.
--
-- This migration adds the recency fields we already compute live so
-- tomorrow's first cron starts populating them. Once 2-3 weeks of data
-- accumulates, Jerry weight tuning can use real drift evidence instead
-- of None placeholders.

ALTER TABLE mlb_game_results
    ADD COLUMN IF NOT EXISTS home_offense_drift NUMERIC,
    ADD COLUMN IF NOT EXISTS away_offense_drift NUMERIC,
    ADD COLUMN IF NOT EXISTS home_ops_last7 NUMERIC,
    ADD COLUMN IF NOT EXISTS away_ops_last7 NUMERIC,
    ADD COLUMN IF NOT EXISTS home_ops_last14 NUMERIC,
    ADD COLUMN IF NOT EXISTS away_ops_last14 NUMERIC,
    ADD COLUMN IF NOT EXISTS home_wrc_proxy_l14 NUMERIC,
    ADD COLUMN IF NOT EXISTS away_wrc_proxy_l14 NUMERIC,
    ADD COLUMN IF NOT EXISTS home_last10_runs_per_game NUMERIC,
    ADD COLUMN IF NOT EXISTS away_last10_runs_per_game NUMERIC,
    ADD COLUMN IF NOT EXISTS home_last5_runs_per_game NUMERIC,
    ADD COLUMN IF NOT EXISTS away_last5_runs_per_game NUMERIC;

COMMENT ON COLUMN mlb_game_results.home_offense_drift IS
  'L10 R/G - season R/G at game time. Positive = hot bats, negative = slumping.
   Used by Jerry Model + props pipeline. Snapshot needed for backtest retroactive
   tuning of recency weights — without this the audit treats drift as None.';

NOTIFY pgrst, 'reload schema';
