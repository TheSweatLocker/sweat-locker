-- 2026-09-15d EMERGENCY — enforce tier whitelist on v_mlb_props_publishable.
-- ================================================================
-- Second Prop Jerry flood of the day. Root cause: COVERAGE tier
-- (used by hits_over to persist rows for Ledger parlay pulls per
-- generate_props.py:388-391 comment) was silently passing the view's
-- Rule 2 ("keep any non-SKIP") and leaking to the app. Today: 145
-- hits_over COVERAGE rows surfaced after LR was banned from the family
-- (fix in backfill_prop_lookback.py) → still visible → still lagged
-- the render loop.
--
-- Also: `is_skip_back` was a SKIP-tier→BACK-verdict opt-in for Jerry's
-- rare high-conviction SKIP flips. Preserved as opt-in path but any
-- non-SKIP non-COVERAGE non-LEAN etc. was never intended to publish.
-- Explicit whitelist kills the ambiguity permanently.
--
-- Ships as Rule 5: publisher-tier whitelist. Tier MUST be one of
-- (PRIME, STRONG, LEAN) OR SKIP-with-Jerry-BACK opt-in. No other
-- tier value gets through — COVERAGE, unknown, NULL all rejected.
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
    (
        p.signals->>'_coverage_kill_gate' IS NULL
        OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no','')
    )
    -- Rule 2: keep any non-SKIP OR SKIP-with-Jerry-BACK
    AND (
        p.tier != 'SKIP'
        OR UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
    )
    -- Rule 3: require L5/L10 stat rows on lower tiers
    AND (
        p.tier IN ('PRIME','STRONG')
        OR (
            jsonb_typeof(p.signals->'_stat_last10') = 'array'
            AND jsonb_array_length(p.signals->'_stat_last10') > 0
        )
    )
    -- Rule 4: batter-family ban (no PROP_TYPE_LABELS support in app)
    AND p.prop_type NOT IN (
        'runs_over',        'runs_under',
        'rbis_over',        'rbis_under',
        'total_bases_over', 'total_bases_under',
        'hr_over',          'hr_under'
    )
    -- Rule 5 (2026-09-15): publisher-tier whitelist. COVERAGE is a
    -- Ledger-only bookkeeping tier per generate_props.py comment; it
    -- was leaking to the app via Rule 2's non-SKIP catchall. Only
    -- these three tiers publish, with SKIP allowed only when Jerry
    -- has flipped it to BACK. Fixes 145 hits_over COVERAGE rows
    -- surviving the 9/15 LR-family exclusion demote.
    AND (
        p.tier IN ('PRIME','STRONG','LEAN')
        OR (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK')
    );

NOTIFY pgrst, 'reload schema';
