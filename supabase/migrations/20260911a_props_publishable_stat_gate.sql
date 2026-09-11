-- 2026-09-11 GATE COVERAGE STUBS WITHOUT L5/L10 DATA
-- ================================================================
-- Problem: on 9/11 the MLB slate produced ~577 COVERAGE-tier stubs
-- from sweep_prop_coverage. These stubs pass the existing view
-- filter because `_coverage_kill_gate` is NULL for them, but their
-- signals dict only carries `_logreg_shadow` (no `_stat_last10`).
-- generate_prop_jerry_synthesis's template renderer relies on
-- `signals._stat_last10` to fill `render_sections.recent_form.rows`
-- — with no rows, the app publishes the prop card WITHOUT the L5/L10
-- bar chart the user expects on every card.
--
-- The user reported: "not seeing graphs in prop jerry the L5/L10 bar
-- graphs ... they were there yesterday in that build". Yesterday
-- (9/10) COVERAGE tier volume was 0; today it's 577 → most cards
-- shipped without their chart.
--
-- Fix: require signals->>'_stat_last10' to be a non-empty JSON array
-- for anything below STRONG. PRIME/STRONG always have full signals
-- populated by generate_props (verified 78/78 today), so keep them
-- unconditional. LEAN can be either — but generate_props reliably
-- fills _stat_last10 there too. COVERAGE + SKIP-with-BACK-verdict are
-- the classes that can leak in without stat data; gate those.
-- ================================================================

CREATE OR REPLACE VIEW public.v_mlb_props_publishable AS
SELECT
    p.game_id,
    p.game_date,
    p.player_name,
    p.player_team,
    p.matchup,
    p.prop_type,
    p.prop_line,
    p.direction,
    p.tier,
    p.conviction,
    p.signals,
    p.book_line,
    p.book_over_odds,
    p.book_under_odds,
    p.result,
    p.refit_conviction,
    COALESCE(p.refit_conviction, p.conviction) AS display_conviction,
    (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK') AS is_skip_back,
    pj.call_verdict AS jerry_verdict,
    pj.short_read  AS jerry_short_read,
    pj.conviction  AS jerry_conviction
FROM mlb_pipeline_props p
LEFT JOIN prop_jerry_reads pj
    ON pj.game_id     = p.game_id
   AND pj.player_name = p.player_name
   AND pj.prop_type   = p.prop_type
   AND pj.direction   = p.direction
   AND pj.sport       = 'MLB'
   AND pj.game_date   = p.game_date
WHERE
    -- Rule 1: drop COVERAGE stubs flagged by the coverage-kill gate.
    (
        p.signals->>'_coverage_kill_gate' IS NULL
        OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no','')
    )
    -- Rule 2: keep any non-SKIP OR SKIP-with-Jerry-BACK
    AND (
        p.tier != 'SKIP'
        OR UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
    )
    -- Rule 3 (2026-09-11): require L5/L10 stat rows for the recent-form
    -- chart on lower tiers. PRIME/STRONG always have them; below that,
    -- gate publication on signals._stat_last10 being a non-empty JSON
    -- array. Kills the ~577 COVERAGE-tier stubs today that would have
    -- shipped as chartless cards.
    AND (
        p.tier IN ('PRIME','STRONG')
        OR (
            jsonb_typeof(p.signals->'_stat_last10') = 'array'
            AND jsonb_array_length(p.signals->'_stat_last10') > 0
        )
    );

COMMENT ON VIEW public.v_mlb_props_publishable IS
    'MLB pipeline props with server-side publishability rules applied. '
    'App reads from this view instead of applying tier/signal filters '
    'client-side. Threshold changes = one SQL edit, no App Store ship.';

NOTIFY pgrst, 'reload schema';
