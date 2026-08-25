-- Prop playbook infrastructure (2026-08-17).
--
-- Ports the game-side signal_sources playbook architecture to props.
-- See project_prop_playbook_design_817 memory for the full design.
--
-- Three additive schema changes (nothing existing is broken):
--   1. Extend signal_sources with subject_scope column so prop signals
--      can live in the same table as game signals
--   2. Create prop_playbook_decisions table for shadow-mode decision
--      history (one row per prop scoring event, supports re-scoring)
--   3. Add per-prop L5/L10 lookback columns to mlb_pipeline_props +
--      nfl_pipeline_props (populated by generate_props at scoring time)
--
-- Shadow-mode: playbook decisions land in prop_playbook_decisions
-- WITHOUT touching mlb_pipeline_props.tier or .conviction. Legacy
-- scoring stays authoritative until 14d of shadow data proves playbook
-- meets or beats legacy hit-rate. Then flip cutover.

-- ─── 1. signal_sources.subject_scope ──────────────────────────────────
-- Existing 148 rows all become subject_scope='game' automatically.
-- New prop signals get subject_scope='prop' or 'player_prop'.
ALTER TABLE public.signal_sources
  ADD COLUMN IF NOT EXISTS subject_scope TEXT NOT NULL DEFAULT 'game';

-- ─── 2. prop_playbook_decisions table ─────────────────────────────────
CREATE TABLE IF NOT EXISTS public.prop_playbook_decisions (
  id            BIGSERIAL PRIMARY KEY,
  sport         TEXT NOT NULL,
  game_date     DATE NOT NULL,
  game_id       TEXT,
  player_name   TEXT NOT NULL,
  prop_type     TEXT NOT NULL,
  direction     TEXT NOT NULL,           -- 'over' | 'under'
  prop_line     NUMERIC,
  playbook_tier TEXT,                     -- PRIME | STRONG | LEAN | PASS
  playbook_conviction INT,
  playbook_side TEXT,                     -- BACK | FADE | PASS
  playbook_score      NUMERIC,
  playbook_margin     NUMERIC,
  playbook_sources    JSONB NOT NULL DEFAULT '[]'::jsonb,  -- audit trail
  legacy_tier         TEXT,               -- captured for diff analysis
  legacy_conviction   INT,
  legacy_refit_conviction INT,
  scored_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ppd_date_sport
  ON public.prop_playbook_decisions (game_date, sport);
CREATE INDEX IF NOT EXISTS idx_ppd_player_prop
  ON public.prop_playbook_decisions (player_name, prop_type, direction, game_date);
CREATE INDEX IF NOT EXISTS idx_ppd_scored_at
  ON public.prop_playbook_decisions (scored_at DESC);

-- ─── 3. Lookback columns on prop tables ───────────────────────────────
-- These are populated by generate_props.py (+ nfl_generate_props.py) at
-- prop-creation time using existing player-stat fetchers. The playbook
-- scorer reads them via `p['player_l10_hit_count']` etc.
ALTER TABLE public.mlb_pipeline_props
  ADD COLUMN IF NOT EXISTS player_l5_hit_count       INT,
  ADD COLUMN IF NOT EXISTS player_l10_hit_count      INT,
  ADD COLUMN IF NOT EXISTS player_season_hit_pct     NUMERIC,
  ADD COLUMN IF NOT EXISTS player_l5_extreme_flag    BOOLEAN,
  ADD COLUMN IF NOT EXISTS player_l10_extreme_flag   BOOLEAN,
  ADD COLUMN IF NOT EXISTS player_lookback_updated_at TIMESTAMPTZ;

ALTER TABLE public.nfl_pipeline_props
  ADD COLUMN IF NOT EXISTS player_l5_hit_count       INT,
  ADD COLUMN IF NOT EXISTS player_l10_hit_count      INT,
  ADD COLUMN IF NOT EXISTS player_season_hit_pct     NUMERIC,
  ADD COLUMN IF NOT EXISTS player_l5_extreme_flag    BOOLEAN,
  ADD COLUMN IF NOT EXISTS player_l10_extreme_flag   BOOLEAN,
  ADD COLUMN IF NOT EXISTS player_lookback_updated_at TIMESTAMPTZ;

NOTIFY pgrst, 'reload schema';
