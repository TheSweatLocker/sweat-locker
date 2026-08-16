-- Ensemble Health — self-regulation state for the scorer (2026-08-16).
--
-- Runs alongside model_health (which tracks per-model V4 suppression).
-- This table tracks the ENSEMBLE-LEVEL health: rolling ROI, hit rate,
-- consecutive cold days. When the ensemble goes cold, the scorer reads
-- the current health row and either soft-tightens thresholds
-- (higher LEAN floor) or hard-suppresses (fall back to legacy
-- compute_primary_play).
--
-- Populated nightly by monitor_ensemble_health.py which:
--   1. Reads the last 30d of resolved games
--   2. Re-scores each with ensemble_scorer and grades against actual result
--   3. Computes rolling hit rate + ROI per market + aggregate
--   4. Compares to thresholds, sets suppression state
--   5. Increments cold_streak_days if below breakeven; resets on green day
--
-- ensemble_scorer reads status_flag at score time (cached per-run):
--   'healthy'         → normal thresholds
--   'watch'           → warning logged, still normal thresholds
--   'soft_tighten'    → LEAN threshold raised (0.5 → 0.8) → fewer LEANs
--   'hard_suppress'   → skip ensemble entirely, fall through to legacy

CREATE TABLE IF NOT EXISTS public.ensemble_health (
  id             BIGSERIAL PRIMARY KEY,
  sport          TEXT NOT NULL,
  computed_date  DATE NOT NULL,

  -- Rolling window measurements
  window_days    INT NOT NULL DEFAULT 30,
  n_picks        INT,
  n_wins         INT,
  n_losses       INT,
  n_pushes       INT,
  hit_rate       NUMERIC,        -- 0-100
  roi_pct        NUMERIC,        -- units-net / units-risked * 100

  -- Per-market breakdown (optional; nullable)
  ml_hit_rate    NUMERIC,
  ml_n           INT,
  rl_hit_rate    NUMERIC,
  rl_n           INT,
  total_hit_rate NUMERIC,
  total_n        INT,

  -- Cold-streak tracking
  cold_streak_days INT NOT NULL DEFAULT 0,   -- consecutive days ROI < 0
  green_streak_days INT NOT NULL DEFAULT 0,  -- consecutive days ROI >= 0

  -- Self-regulation state
  status_flag    TEXT NOT NULL DEFAULT 'healthy',
    -- 'healthy'         — rolling ROI >= 0
    -- 'watch'           — ROI turned negative in last day, streak = 1
    -- 'soft_tighten'    — 5+ consecutive cold days → raise LEAN threshold
    -- 'hard_suppress'   — 10+ consecutive cold days → fall back to legacy
  lean_threshold_override NUMERIC,  -- e.g. 0.8 when soft_tighten fires
  notes          TEXT,

  UNIQUE (sport, computed_date)
);

CREATE INDEX IF NOT EXISTS ensemble_health_current_idx
  ON public.ensemble_health (sport, computed_date DESC);

COMMENT ON TABLE public.ensemble_health IS
  'Ensemble-level health tracking + self-regulation state. Read by ensemble_scorer to auto-tighten or suppress when cold. Populated by monitor_ensemble_health.py nightly.';

NOTIFY pgrst, 'reload schema';
