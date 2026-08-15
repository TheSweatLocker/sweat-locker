-- Signal Registry — unified ledger of every signal we track (2026-08-15 pm).
--
-- One row per signal with its historical hit rate, sample size, tier
-- (VALIDATED / DISCOVERY / UNVALIDATED / ANTI_VALIDATED), and recommended
-- weight. Sourced from docs/PLAYBOOK.md Part 1.
--
-- Purpose: single auditable table that every decision maker (play_of_day,
-- Jerry synthesis prompt, primary_play resolver, tier gates) can read to
-- justify picks with actual historical basis. Replaces scattered signal
-- weighting hardcoded across the pipeline.

CREATE TABLE IF NOT EXISTS public.signal_registry (
  id              BIGSERIAL PRIMARY KEY,
  signal_name     TEXT NOT NULL,
  category        TEXT NOT NULL,           -- 'model' | 'pitcher' | 'team_offense' | 'bullpen' | 'public_splits' | 'line_movement' | 'cohort' | 'pattern' | 'refit' | 'external' | 'situational'
  sport           TEXT NOT NULL,           -- 'MLB' | 'NFL' | ... | '*' (universal)
  market_scope    TEXT,                    -- 'ml' | 'spread' | 'total' | 'prop' | 'multi'
  description     TEXT,

  -- Historical performance
  hit_rate        NUMERIC,                 -- % of times signal side won
  sample_n        INT,
  edge_pp         NUMERIC,                 -- hit_rate - baseline (e.g. 52.4 for -110)

  -- Tier + weighting
  tier            TEXT NOT NULL DEFAULT 'UNVALIDATED',
    -- 'VALIDATED' (n≥50, edge≥5pp) → full weight
    -- 'DISCOVERY' (n≥15, positive edge) → half weight
    -- 'UNVALIDATED' (no backtest) → info-only
    -- 'ANTI_VALIDATED' (backtest below baseline) → invert or drop
  recommended_weight NUMERIC NOT NULL DEFAULT 0,

  -- Direction the signal points
  direction_hint  TEXT,                    -- 'FOLLOW' | 'FADE' | 'NEUTRAL' | 'ADJUST'

  -- Provenance
  origin          TEXT,                    -- 'SEEDED' | 'BACKTEST_20260815' | 'DISCOVERED' | 'MEMORY'
  last_computed_at TIMESTAMPTZ,
  notes           TEXT,

  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE (signal_name, sport, market_scope)
);

CREATE INDEX IF NOT EXISTS signal_registry_tier_idx
  ON public.signal_registry (tier, sport);
CREATE INDEX IF NOT EXISTS signal_registry_category_idx
  ON public.signal_registry (category, sport);

COMMENT ON TABLE public.signal_registry IS
  'Single source of truth for every signal in the pipeline. Read by play_of_day, Jerry synthesis. Populated from docs/PLAYBOOK.md.';

NOTIFY pgrst, 'reload schema';
