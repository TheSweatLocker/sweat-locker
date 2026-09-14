-- 2026-09-13 SIGNAL ATTRIBUTION TABLE — per-game per-signal audit trail
-- ================================================================
-- Andy 9/13: "we should be tracking what each says and keeping record."
-- Every signal chip we surface on the game card (EPA gap, LR shadow,
-- GOAT, cohort tags, anchor, CPOE, etc.) should snapshot at pick-lock
-- time + grade against the game result. Rollup gives us per-signal
-- hit rate that back-populates into the chip tooltip ("LR agrees hits
-- 62% n=87"), plus tuning data for the weekly signal weight refit.
--
-- MOAT: real per-signal performance data, not intuition. Answers
-- "when GOAT agrees with our pick, do we hit more?" and "which cohort
-- tags are actually predictive vs decorative?" with hard numbers.
-- ================================================================

CREATE TABLE IF NOT EXISTS public.signal_attribution (
    id BIGSERIAL PRIMARY KEY,
    sport TEXT NOT NULL,                    -- NFL, NCAAF, MLB, NBA, NHL, NCAAB, UFC
    game_id TEXT NOT NULL,
    game_date DATE NOT NULL,
    season INTEGER,
    season_week INTEGER,                    -- from nfl_game_context.season_week

    signal_key TEXT NOT NULL,               -- 'EPA_GAP', 'LR_SHADOW', 'GOAT', 'HEAVY_HOME_DOG', 'ANCHOR', 'CPOE_GAP', 'DIV_GAME', ...
    signal_value NUMERIC,                   -- numeric magnitude when applicable (EPA gap = 0.15, LR p = 0.68)
    signal_side TEXT,                       -- 'HOME' / 'AWAY' / 'OVER' / 'UNDER' / 'NEUTRAL'
    kind TEXT NOT NULL,                     -- 'ok' (agrees w/ pick) | 'warn' (disagrees) | 'neutral' (informational)

    pick_market TEXT,                       -- 'ml' / 'spread' / 'rl' / 'total' — from primary_play at snapshot
    pick_side TEXT,                         -- HOME / AWAY / OVER / UNDER — from primary_play
    pick_tier TEXT,                         -- PRIME / STRONG / LEAN / COVERAGE / PASS

    snapshot_at TIMESTAMPTZ DEFAULT NOW(),  -- when signal was captured (typically at Thu-lock / 11am ET)

    -- Result grading (populated by resolver post-game)
    result TEXT,                            -- W / L / P / null while pending
    resolved_at TIMESTAMPTZ,
    close_line NUMERIC,                     -- market spread / total at close for retro grading
    actual_margin NUMERIC,                  -- home_score - away_score
    actual_total NUMERIC
);

-- Unique per (game_id, signal_key) so re-snapshots update in place
-- (agreement can flip if primary_play shifts between morning + lock).
CREATE UNIQUE INDEX IF NOT EXISTS signal_attribution_uniq
    ON public.signal_attribution (sport, game_id, signal_key);

CREATE INDEX IF NOT EXISTS signal_attribution_sport_signal_result
    ON public.signal_attribution (sport, signal_key, kind, result);

CREATE INDEX IF NOT EXISTS signal_attribution_game_date
    ON public.signal_attribution (game_date DESC);

COMMENT ON TABLE public.signal_attribution IS
    'Per-game per-signal snapshot + grading. One row per (game, signal). '
    'Written by snapshot_signals_at_lock.py at Sharp Card lock time. '
    'Graded by grade_signal_attribution.py after game_results resolve. '
    'Rolled up into signal_records matview for per-signal hit rate. '
    'Consumed by game card SignalsRow tooltip + weekly signal weight refit.';

-- ================================================================
-- Rollup view: per-signal hit rate over last 30/90/lifetime windows
-- ================================================================

CREATE OR REPLACE VIEW public.v_signal_records AS
WITH graded AS (
    SELECT
        sport,
        signal_key,
        kind,
        result,
        game_date,
        pick_tier
    FROM public.signal_attribution
    WHERE result IN ('W', 'L', 'P')
),
d30 AS (
    SELECT
        sport, signal_key, kind,
        COUNT(*) FILTER (WHERE result = 'W')::INT AS w30,
        COUNT(*) FILTER (WHERE result = 'L')::INT AS l30,
        COUNT(*) FILTER (WHERE result = 'P')::INT AS p30
    FROM graded
    WHERE game_date >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY sport, signal_key, kind
),
d90 AS (
    SELECT
        sport, signal_key, kind,
        COUNT(*) FILTER (WHERE result = 'W')::INT AS w90,
        COUNT(*) FILTER (WHERE result = 'L')::INT AS l90
    FROM graded
    WHERE game_date >= CURRENT_DATE - INTERVAL '90 days'
    GROUP BY sport, signal_key, kind
),
lifetime AS (
    SELECT
        sport, signal_key, kind,
        COUNT(*) FILTER (WHERE result = 'W')::INT AS w_all,
        COUNT(*) FILTER (WHERE result = 'L')::INT AS l_all
    FROM graded
    GROUP BY sport, signal_key, kind
)
SELECT
    l.sport, l.signal_key, l.kind,
    COALESCE(d30.w30, 0) AS wins_30d,
    COALESCE(d30.l30, 0) AS losses_30d,
    COALESCE(d30.p30, 0) AS pushes_30d,
    CASE WHEN (COALESCE(d30.w30,0) + COALESCE(d30.l30,0)) > 0
         THEN ROUND(100.0 * COALESCE(d30.w30, 0) / (COALESCE(d30.w30,0) + COALESCE(d30.l30,0)), 1)
         ELSE NULL END AS hit_pct_30d,
    COALESCE(d90.w90, 0) AS wins_90d,
    COALESCE(d90.l90, 0) AS losses_90d,
    CASE WHEN (COALESCE(d90.w90,0) + COALESCE(d90.l90,0)) > 0
         THEN ROUND(100.0 * COALESCE(d90.w90, 0) / (COALESCE(d90.w90,0) + COALESCE(d90.l90,0)), 1)
         ELSE NULL END AS hit_pct_90d,
    COALESCE(l.w_all, 0) AS wins_lifetime,
    COALESCE(l.l_all, 0) AS losses_lifetime,
    CASE WHEN (COALESCE(l.w_all,0) + COALESCE(l.l_all,0)) > 0
         THEN ROUND(100.0 * COALESCE(l.w_all, 0) / (COALESCE(l.w_all,0) + COALESCE(l.l_all,0)), 1)
         ELSE NULL END AS hit_pct_lifetime
FROM lifetime l
LEFT JOIN d30 ON l.sport = d30.sport AND l.signal_key = d30.signal_key AND l.kind = d30.kind
LEFT JOIN d90 ON l.sport = d90.sport AND l.signal_key = d90.signal_key AND l.kind = d90.kind
WHERE (COALESCE(l.w_all,0) + COALESCE(l.l_all,0)) >= 5;   -- suppress tiny-sample noise

COMMENT ON VIEW public.v_signal_records IS
    'Per-(sport, signal_key, kind) hit rate rollup over 30d / 90d / '
    'lifetime windows. Sample gate: 5+ graded to appear. Client reads '
    'this to enrich the SignalsRow tooltip with concrete track record.';

-- Public read
ALTER TABLE public.signal_attribution ENABLE ROW LEVEL SECURITY;
CREATE POLICY signal_attribution_read ON public.signal_attribution
    FOR SELECT USING (TRUE);
GRANT SELECT ON public.signal_attribution TO anon, authenticated;
GRANT SELECT ON public.v_signal_records TO anon, authenticated;
GRANT ALL ON public.signal_attribution TO service_role;
GRANT USAGE, SELECT ON SEQUENCE signal_attribution_id_seq TO service_role;

NOTIFY pgrst, 'reload schema';
