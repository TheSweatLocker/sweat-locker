-- clv_weekly_rollup — Sunday audit surface for CLV tracking (2026-08-15 pm).
--
-- Populated by clv_weekly_rollup.py — aggregates clv_snapshots by sport ×
-- window (7d / 30d / 90d) with per-market / per-tier / per-model breakdown
-- stored as JSONB. Each Sunday run appends a snapshot row so we retain the
-- weekly trajectory of model + tier CLV performance.
--
-- Used by: weekly Sunday audit + future observability dashboard.

CREATE TABLE IF NOT EXISTS public.clv_weekly_rollup (
  id             BIGSERIAL PRIMARY KEY,
  sport          TEXT NOT NULL,
  window_days    INT  NOT NULL,       -- 7 | 30 | 90
  n              INT,
  avg_clv        NUMERIC,
  beat_rate      NUMERIC,             -- % of picks with clv > 0
  by_market      JSONB,               -- {spread: {n, avg_clv, beat_rate}, ...}
  by_tier        JSONB,               -- {PRIME: {...}, STRONG: {...}, LEAN: {...}}
  by_model       JSONB,               -- {jerry: {...}, panel: {...}, ...}
  computed_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS clv_weekly_rollup_sport_idx
  ON public.clv_weekly_rollup (sport, computed_at DESC);

COMMENT ON TABLE public.clv_weekly_rollup IS
  'Weekly aggregate CLV per sport × window. Populated Sunday by clv_weekly_rollup.py.';

NOTIFY pgrst, 'reload schema';
