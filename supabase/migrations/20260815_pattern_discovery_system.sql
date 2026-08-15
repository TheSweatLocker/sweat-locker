-- Living pattern-discovery system — sport-universal (2026-08-15).
--
-- Turns line movement + public splits into a self-calibrating pattern
-- library. Every snapshot from every source is archived permanently
-- (not just the 14d line_history window). Nightly miner recomputes
-- hit rates per pattern and auto-discovers new combinations.
--
-- Three new tables:
--
-- 1. public_splits_archive — permanent side-by-side OC + Fadereport
--    snapshot per (sport, game_id, market, ts). This is what the pattern
--    miner joins with game results to score "public agreed / disagreed
--    / one-loud-one-silent" hypotheses.
--
-- 2. pattern_registry — named patterns with condition JSONB + rolling
--    hit_rate/n/edge_pp per sport. Miner writes here; play_of_day and
--    cohort code READ here. Every pattern lives in one of four tiers:
--    DISCOVERY  — auto-surfaced, needs more data; NOT consumed downstream
--    VALIDATED  — cleared n + hit-rate threshold; drivers activate
--    DECAYED    — was validated but 30d rate dropped below baseline
--    RETIRED    — decayed 90d+ with no recovery; excluded from mining
--
-- 3. pattern_hits — per-game evaluation history. When a pattern fires
--    on a game, one row here with (pattern_id, game_id, direction_bet,
--    outcome, computed_at). Lets us backtest any pattern's evolution
--    over time.

-- ─── #1 · public_splits_archive ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.public_splits_archive (
  id             BIGSERIAL PRIMARY KEY,
  sport          TEXT NOT NULL,
  game_id        TEXT NOT NULL,
  market         TEXT NOT NULL,          -- 'ml' | 'spread' | 'total'
  pick_side      TEXT NOT NULL,          -- 'HOME' | 'AWAY' | 'OVER' | 'UNDER'

  -- OddsCrowd split (money = handle share; bets = ticket share)
  oc_money_pct   NUMERIC,
  oc_bets_pct    NUMERIC,
  oc_divergence  NUMERIC,

  -- Fadereport split (handle = handle share; bettors = ticket share)
  fr_handle_pct  NUMERIC,
  fr_bettors_pct NUMERIC,

  -- Line snapshot at same instant (nullable, best-effort join)
  current_line   NUMERIC,
  current_odds   INTEGER,

  captured_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (sport, game_id, market, pick_side, captured_at)
);

CREATE INDEX IF NOT EXISTS psa_sport_game_market_idx
  ON public.public_splits_archive (sport, game_id, market, captured_at DESC);
CREATE INDEX IF NOT EXISTS psa_captured_idx
  ON public.public_splits_archive (captured_at DESC);

COMMENT ON TABLE public.public_splits_archive IS
  'Permanent side-by-side archive of OddsCrowd + Fadereport public splits. Never expires.';

-- ─── #2 · pattern_registry ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.pattern_registry (
  id             BIGSERIAL PRIMARY KEY,
  sport          TEXT NOT NULL,          -- 'MLB'|'NFL'|'NCAAF'|'NCAAB'|'NHL'|'UFC'|'*' (universal)
  name           TEXT NOT NULL,
  description    TEXT,

  -- Rule engine format — a list of dict conditions like:
  --   [{"field": "oc_money_pct", "op": ">=", "value": 60},
  --    {"field": "fr_handle_pct", "op": ">=", "value": 60}]
  -- Evaluated by miner on each candidate (game × market × side).
  conditions     JSONB NOT NULL,

  -- What direction does the pattern bet? Values:
  --   'FOLLOW' — bet WITH the side matching conditions
  --   'FADE'   — bet AGAINST the side matching conditions
  --   'NEUTRAL' — pattern is descriptive (record-keeping)
  bet_direction  TEXT NOT NULL DEFAULT 'FOLLOW',

  -- Rolling stats, updated nightly by miner
  hit_rate       NUMERIC,
  n              INTEGER,
  hit_rate_30d   NUMERIC,
  n_30d          INTEGER,
  edge_pp        NUMERIC,               -- hit_rate - baseline

  -- Lifecycle tier
  tier           TEXT NOT NULL DEFAULT 'DISCOVERY',
  -- DISCOVERY | VALIDATED | DECAYED | RETIRED

  -- Provenance
  origin         TEXT NOT NULL DEFAULT 'SEEDED',
  -- SEEDED (hand-coded)  |  DISCOVERED (auto-surfaced)

  last_computed_at TIMESTAMPTZ,
  created_at     TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE (sport, name)
);

CREATE INDEX IF NOT EXISTS pattern_registry_tier_idx
  ON public.pattern_registry (sport, tier, edge_pp DESC);

COMMENT ON COLUMN public.pattern_registry.tier IS
  'DISCOVERY (auto-found, needs data) | VALIDATED (drivers active) | DECAYED (lost edge) | RETIRED';

-- ─── #3 · pattern_hits (per-game evaluation history) ───────────────────

CREATE TABLE IF NOT EXISTS public.pattern_hits (
  id             BIGSERIAL PRIMARY KEY,
  pattern_id     BIGINT NOT NULL REFERENCES public.pattern_registry(id) ON DELETE CASCADE,
  sport          TEXT NOT NULL,
  game_id        TEXT NOT NULL,
  market         TEXT NOT NULL,
  pick_side      TEXT NOT NULL,          -- direction the pattern bet
  outcome        TEXT,                   -- 'HIT' | 'MISS' | 'PUSH' | 'PENDING'
  game_date      DATE NOT NULL,
  captured_at    TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE (pattern_id, game_id, market, pick_side)
);

CREATE INDEX IF NOT EXISTS pattern_hits_pattern_idx
  ON public.pattern_hits (pattern_id, game_date DESC);
CREATE INDEX IF NOT EXISTS pattern_hits_outcome_idx
  ON public.pattern_hits (sport, outcome, game_date DESC);

-- ─── #4 · Extend line_history retention ────────────────────────────────
-- Drop the 14d retention window — pattern miner needs full history to
-- backtest across seasons. New retention: unbounded (managed by partition
-- if needed later).
-- NOTE: If a scheduled DELETE job or trigger exists on line_history for
-- 14d purge, it lives outside this SQL and must be disabled separately.
COMMENT ON TABLE public.line_history IS
  'Per-book odds snapshot time-series. Retention EXTENDED 2026-08-15 for pattern mining — no auto-purge.';

NOTIFY pgrst, 'reload schema';
