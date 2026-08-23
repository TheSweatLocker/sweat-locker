-- public_splits_v2 — source-agnostic long-form splits (2026-08-23 Phase 1)
--
-- Prior state: each split source had dedicated columns/tables
--   public_splits_archive.oc_money_pct, .oc_bets_pct, .fr_handle_pct, etc.
--   fadereport_signals (MLB-only), cleatz_signals — one table per source
-- Adding a new source required schema + app changes. Doesn't scale.
--
-- New: one row per (game, market, side, source, metric). Adding a source
-- becomes an INSERT. App reads game_context.splits_summary JSONB (added
-- as a companion column, populated by the aggregator).
--
-- Sources land here:
--   'oc' = OddsCrowd
--   'fr' = Fadereport
--   'cz' = Cleatz
--   'so' = ScoresAndOdds (Phase 2 build)
--   'pin' = Pinnacle (future)
-- Markets: 'ml' | 'rl' | 'spread' | 'total' | 'moneyline'
-- Metrics: 'money_pct' | 'bets_pct' | 'handle_pct' | 'divergence' | 'strength_pts'

CREATE TABLE IF NOT EXISTS public.public_splits_v2 (
  id             BIGSERIAL PRIMARY KEY,
  snapshot_ts    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sport          TEXT NOT NULL,
  game_id        TEXT NOT NULL,
  market         TEXT NOT NULL,
  side           TEXT NOT NULL,
  source         TEXT NOT NULL,
  metric         TEXT NOT NULL,
  value          NUMERIC,
  -- Optional trace fields — helpful for backfill provenance + audits
  source_url     TEXT,
  raw_scrape     JSONB,
  CONSTRAINT public_splits_v2_uniq UNIQUE (game_id, market, side, source, metric, snapshot_ts)
);

CREATE INDEX IF NOT EXISTS public_splits_v2_sport_game_idx
  ON public.public_splits_v2 (sport, game_id, snapshot_ts DESC);
CREATE INDEX IF NOT EXISTS public_splits_v2_source_idx
  ON public.public_splits_v2 (source, snapshot_ts DESC);
CREATE INDEX IF NOT EXISTS public_splits_v2_recent_idx
  ON public.public_splits_v2 (snapshot_ts DESC);

COMMENT ON TABLE public.public_splits_v2 IS
  'Source-agnostic long-form public splits. Adding a source = insert rows, no schema change. Aggregator writes game_context.splits_summary.';

-- Add splits_summary JSONB to each per-sport game_context table.
-- Written by the aggregator after all sources for a game land. App reads
-- ONLY this column — source-specific reads (game_context.oddscrowd_snapshot,
-- fadereport_signals joins) get deprecated post-cutover.
--
-- IF NOT EXISTS on each so re-applying is safe across environments.
ALTER TABLE IF EXISTS public.mlb_game_context
  ADD COLUMN IF NOT EXISTS splits_summary JSONB;
ALTER TABLE IF EXISTS public.nfl_game_context
  ADD COLUMN IF NOT EXISTS splits_summary JSONB;
ALTER TABLE IF EXISTS public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS splits_summary JSONB;
ALTER TABLE IF EXISTS public.ncaab_game_context
  ADD COLUMN IF NOT EXISTS splits_summary JSONB;
ALTER TABLE IF EXISTS public.nba_game_context
  ADD COLUMN IF NOT EXISTS splits_summary JSONB;
ALTER TABLE IF EXISTS public.nhl_game_context
  ADD COLUMN IF NOT EXISTS splits_summary JSONB;

NOTIFY pgrst, 'reload schema';
