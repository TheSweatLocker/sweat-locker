-- Signal Sources — every signal the ensemble scorer can evaluate (2026-08-16).
--
-- North star: adding a new signal (pitcher on a heater, umpire fade,
-- BABIP regression flag, cross-source sharp confirmation, external
-- handicapper pick, cohort match, etc.) should require ONE row insert
-- here — never a code change to the scorer.
--
-- Companion to signal_registry:
--   * signal_sources → HOW to detect the signal on a given game
--     (condition to check, side it favors, strength calculation)
--   * signal_registry → HOW WELL the signal has performed historically
--     (hit_rate, sample_n, tier, recommended_weight)
--
-- The scorer iterates signal_sources for the sport/market, evaluates
-- each condition against the game's context row, and for every source
-- that fires it looks up the weight in signal_registry via signal_key.
--
-- Expression evaluation uses a locked-down evaluator (signal_expr.py)
-- that ONLY allows attribute/dict access on `ctx`, comparisons, arithmetic,
-- and a whitelist of safe functions (abs, min, max, float, int, len, None).
-- No __builtins__, no imports, no attribute access outside ctx.

CREATE TABLE IF NOT EXISTS public.signal_sources (
  id             BIGSERIAL PRIMARY KEY,
  signal_key     TEXT NOT NULL,              -- unique key, matches signal_registry.signal_name
  sport          TEXT NOT NULL,              -- 'MLB' | 'NFL' | 'NCAAF' | 'NCAAB' | 'UFC' | '*' (universal)
  class          TEXT NOT NULL,              -- 'model' | 'cohort' | 'scenario' | 'split' | 'line_move' |
                                              -- 'external_pick' | 'situational' | 'pitcher' | 'offense' |
                                              -- 'bullpen' | 'defense' | 'stat_projection' | 'weather' |
                                              -- 'umpire' | 'park' | 'refit'
  market_scope   TEXT NOT NULL DEFAULT 'multi', -- 'ml' | 'rl' | 'total' | 'multi'

  -- HOW to detect the signal fires
  --   condition_expr: Python expression, returns True when signal applies.
  --     Access `ctx.field_name` or `ctx["field_name"]`. Reference other
  --     ctx fields freely. Missing fields evaluate to None; comparisons
  --     with None short-circuit to False. Example:
  --       "ctx.home_sp_l3_era is not None and float(ctx.home_sp_l3_era) < 2.5"
  condition_expr TEXT NOT NULL,

  -- HOW to determine which side the signal favors
  --   side_expr: Python expression, returns one of the standard candidate
  --     labels: 'HOME_ML', 'AWAY_ML', 'HOME_RL', 'AWAY_RL', 'OVER', 'UNDER'.
  --     Static string is fine for signals with a fixed direction
  --     (e.g. "'UNDER'" for a suppressed-offense flag). Expression
  --     when direction depends on the data (e.g. sharp side follows ctx).
  side_expr      TEXT NOT NULL,

  -- HOW strong the signal is [0, 1]
  --   strength_expr: Python expression, returns float in [0, 1].
  --     0.5 = default moderate strength.
  strength_expr  TEXT NOT NULL DEFAULT '0.5',

  -- WHERE the historical hit rate comes from
  --   Reads signal_registry (signal_name = weight_registry_key) at score
  --   time. If NULL, uses UNVALIDATED floor (0.15 weight until backtest).
  weight_registry_key TEXT,

  -- Optional inline hit_rate override (skip signal_registry lookup)
  hit_rate_pct   NUMERIC,                    -- 0-100, hit rate as %
  sample_n       INTEGER,                    -- sample size behind hit_rate_pct

  -- HOW to render this signal to a user
  --   display_prose_template: plain-English narration for Jerry to quote.
  --   {vars} interpolated at render time. Example:
  --     "{pitcher_name} on a heater — {l3_era} ERA last three vs {season_era} season"
  --   NEVER references internal keys, class names, or signal_registry lingo.
  display_prose_template TEXT,

  -- WHY this signal exists (docstring for humans)
  description    TEXT,

  -- LIFECYCLE
  enabled        BOOLEAN NOT NULL DEFAULT TRUE,
  origin         TEXT,                       -- 'SEEDED' | 'DISCOVERED_YYYYMMDD' | 'MANUAL'
  created_at     TIMESTAMPTZ DEFAULT NOW(),
  updated_at     TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE (signal_key, sport, market_scope)
);

CREATE INDEX IF NOT EXISTS signal_sources_lookup_idx
  ON public.signal_sources (sport, market_scope, enabled)
  WHERE enabled = TRUE;

CREATE INDEX IF NOT EXISTS signal_sources_class_idx
  ON public.signal_sources (class, sport);

COMMENT ON TABLE public.signal_sources IS
  'Every signal the ensemble scorer evaluates. Adding a signal = INSERT row. Companion to signal_registry (which stores historical performance).';

COMMENT ON COLUMN public.signal_sources.condition_expr IS
  'Python expression evaluated against ctx. Returns True when the signal fires. Runs in a locked-down evaluator (no builtins/imports).';

COMMENT ON COLUMN public.signal_sources.display_prose_template IS
  'Plain-English narration for Jerry. Reader-friendly language only — never internal keys, model names, or registry jargon.';

NOTIFY pgrst, 'reload schema';
