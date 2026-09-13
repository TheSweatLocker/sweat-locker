-- 2026-09-13 DISPLAY_CONVICTION NULLIF-ZERO BACKSTOP
-- ================================================================
-- Root cause: v_mlb_props_publishable + v_nfl_props_publishable defined
-- display_conviction as COALESCE(refit_conviction, conviction). COALESCE
-- substitutes only on NULL, so any row with refit_conviction=0 displayed
-- as 0 — the app's `.order('display_conviction', {ascending: false})`
-- then sorted these to the bottom, effectively hiding them.
--
-- Impact (verified 5-day slice 9/9-9/13): 5-8 bb_over/bb_under PRIMEs
-- daily were literally invisible on Prop Jerry because their stale
-- refit_conviction=0 from an Aug 31 model snapshot took priority over
-- their legitimate 73-77 legacy conviction. Andy: "we only have two that
-- aren't hits on a full slate. Doesnt make sense" — because 6 real
-- pitcher PRIMEs were hidden.
--
-- Belt-and-suspenders fix has three layers, this migration is the
-- structural backstop:
--   1. apply_prop_refit.py compute_refit no longer emits 0.0 (returns
--      None when the raw score puts conviction at floor).
--   2. apply_prop_refit.py stale-zero cleanup NULLs any existing
--      refit_conviction=0 rows encountered on every run.
--   3. THIS: NULLIF(refit_conviction, 0) inside COALESCE so even if a
--      zero slips through the code fixes, display_conviction still
--      falls back to legacy conviction and the pick surfaces.
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
    -- 2026-09-13: NULLIF(refit,0) so stale-zero refit values never
    -- override legacy conviction. COALESCE alone treats 0 as valid and
    -- hides real PRIMEs on the app's display-conviction sort.
    COALESCE(NULLIF(p.refit_conviction, 0), p.conviction) AS display_conviction,
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
    AND (
        p.tier != 'SKIP'
        OR UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
    )
    AND (
        p.tier IN ('PRIME','STRONG')
        OR (
            jsonb_typeof(p.signals->'_stat_last10') = 'array'
            AND jsonb_array_length(p.signals->'_stat_last10') > 0
        )
    );

COMMENT ON VIEW public.v_mlb_props_publishable IS
    'MLB pipeline props with server-side publishability rules applied. '
    '2026-09-13 update: display_conviction uses NULLIF(refit,0) as third '
    'backstop against stale-zero refit values hiding real PRIMEs on sort.';

-- Mirror the fix on NFL view for parity.
CREATE OR REPLACE VIEW public.v_nfl_props_publishable AS
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
    -- 2026-09-13: same NULLIF(refit,0) backstop as MLB view.
    COALESCE(NULLIF(p.refit_conviction, 0), p.conviction) AS display_conviction,
    (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK') AS is_skip_back,
    pj.call_verdict AS jerry_verdict,
    pj.short_read  AS jerry_short_read,
    pj.conviction  AS jerry_conviction
FROM nfl_pipeline_props p
LEFT JOIN prop_jerry_reads pj
    ON pj.game_id     = p.game_id
   AND pj.player_name = p.player_name
   AND pj.prop_type   = p.prop_type
   AND pj.direction   = p.direction
   AND pj.sport       = 'NFL'
   AND pj.game_date   = p.game_date
WHERE
    (
        p.signals->>'_coverage_kill_gate' IS NULL
        OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no','')
    )
    AND (
        p.tier != 'SKIP'
        OR UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
    )
    AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
    AND COALESCE(pj.conviction, 0) >= 60;

COMMENT ON VIEW public.v_nfl_props_publishable IS
    'NFL pipeline props with server-side publishability rules applied. '
    '2026-09-13 update: display_conviction uses NULLIF(refit,0) as third '
    'backstop against stale-zero refit values (parity with MLB view).';

NOTIFY pgrst, 'reload schema';
