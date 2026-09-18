-- 2026-09-17i — NFL shadow_v2 infrastructure for Phase 0 buildout.
-- ==================================================================
-- Andy directive: state-of-the-art NFL rebuild in phases. Every new
-- signal / model change must shadow-validate ≥4 weeks + beat live by
-- ≥3pp on n≥50 before promoting. This migration adds the columns
-- shadow logic writes to WITHOUT touching the live primary_play /
-- playbook_snapshot paths.
--
-- Design decision: dedicated columns rather than shoving shadow into
-- primary_play._shadow_v2 key. Reason: keeps grader queries clean
-- (WHERE shadow_v2 IS NOT NULL vs. WHERE primary_play->>'_shadow_v2'
-- IS NOT NULL), enables independent RLS if we ever expose shadow to
-- team members, and matches the pattern MLB used for cohorts_v2.
--
-- Columns added:
--   nfl_game_context.primary_play_shadow_v2 (jsonb)
--     — full alt primary_play produced by candidate new logic.
--       Same shape as live primary_play: {type, side, label, tier,
--       conviction, _engine, _signals, ...}
--   nfl_game_context.shadow_v2_generated_at (timestamptz)
--     — when the shadow was last computed for this game
--   nfl_pipeline_props.playbook_snapshot_shadow_v2 (jsonb)
--     — alt playbook signals blob (matches live shape)
--   nfl_pipeline_props.shadow_v2_generated_at (timestamptz)
--
-- Parallel for NCAAF (same phased buildout planned once NFL stable).
--
-- ROLLBACK: DROP COLUMN on each (columns are NULL by default so no
-- data at risk).
-- ==================================================================

ALTER TABLE public.nfl_game_context
    ADD COLUMN IF NOT EXISTS primary_play_shadow_v2 jsonb,
    ADD COLUMN IF NOT EXISTS shadow_v2_generated_at timestamptz;

ALTER TABLE public.nfl_pipeline_props
    ADD COLUMN IF NOT EXISTS playbook_snapshot_shadow_v2 jsonb,
    ADD COLUMN IF NOT EXISTS shadow_v2_generated_at timestamptz;

-- Same for NCAAF — build parallel from day 1 so we don't have to
-- re-migrate later. IF EXISTS guard for pre-launch safety.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'ncaaf_game_context') THEN
        EXECUTE 'ALTER TABLE public.ncaaf_game_context
                 ADD COLUMN IF NOT EXISTS primary_play_shadow_v2 jsonb,
                 ADD COLUMN IF NOT EXISTS shadow_v2_generated_at timestamptz';
    END IF;
END $$;

-- Backtest harness result table — stores shadow-vs-live grade rollups
-- so we can trend "does shadow_v2 beat live over the last N weeks"
-- without recomputing on every dashboard render. One row per
-- (sport, market, variant, week, tier, side).
CREATE TABLE IF NOT EXISTS public.shadow_v2_backtest_results (
    id                   bigserial PRIMARY KEY,
    sport                text NOT NULL,
    market               text NOT NULL,   -- 'ml' | 'rl' | 'total' | 'prop'
    variant              text NOT NULL,   -- e.g. 'usage_v2', 'wopr_gate', 'yac_signal'
    game_date            date,
    season               int,
    season_week          int,
    tier                 text,            -- shadow tier assigned
    side                 text,
    picks_count          int NOT NULL DEFAULT 0,
    wins                 int NOT NULL DEFAULT 0,
    losses               int NOT NULL DEFAULT 0,
    pushes               int NOT NULL DEFAULT 0,
    live_hit_rate        numeric,         -- for same picks, live tier's rate
    shadow_hit_rate      numeric,         -- shadow variant's rate
    edge_pp              numeric,         -- shadow_hit_rate - live_hit_rate (in pp)
    n_min_ok             boolean,         -- true if picks_count >= 50 (promo gate)
    computed_at          timestamptz NOT NULL DEFAULT NOW(),
    notes                text,
    UNIQUE (sport, market, variant, game_date, tier, side)
);

CREATE INDEX IF NOT EXISTS idx_shadow_v2_backtest_variant_recent
    ON public.shadow_v2_backtest_results (sport, variant, game_date DESC);

-- RLS: read-only public, admin write. Matches sport_registry pattern.
ALTER TABLE public.shadow_v2_backtest_results ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS shadow_v2_backtest_read ON public.shadow_v2_backtest_results;
CREATE POLICY shadow_v2_backtest_read
    ON public.shadow_v2_backtest_results
    FOR SELECT
    USING (true);

DROP POLICY IF EXISTS shadow_v2_backtest_write ON public.shadow_v2_backtest_results;
CREATE POLICY shadow_v2_backtest_write
    ON public.shadow_v2_backtest_results
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

NOTIFY pgrst, 'reload schema';
