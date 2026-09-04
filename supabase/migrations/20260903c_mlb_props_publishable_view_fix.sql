-- 2026-09-03 FIX for v_mlb_props_publishable
-- ================================================================
-- Original view (20260903b) cast signals._coverage_kill_gate as
-- boolean but actual DB values are strings like
-- "COVERAGE_TIER_UNPUBLISHABLE" — not 'true'/'false'.
-- PostgREST returned: "invalid input syntax for type boolean:
--                      COVERAGE_TIER_UNPUBLISHABLE"
--
-- Fix: treat any non-null, non-'false' value as truthy (matches the
-- prior JavaScript `if (kill) return false;` truthy check).
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
    -- Rule 1: drop COVERAGE stubs. _coverage_kill_gate is stored as
    -- either NULL (publishable) or a string like
    -- 'COVERAGE_TIER_UNPUBLISHABLE' (kill). Any non-null non-'false'
    -- value = kill (matches prior client-side truthy check).
    (
        p.signals->>'_coverage_kill_gate' IS NULL
        OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no','')
    )
    -- Rule 2: keep any non-SKIP OR SKIP-with-Jerry-BACK
    AND (
        p.tier != 'SKIP'
        OR UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
    );

COMMENT ON VIEW public.v_mlb_props_publishable IS
    'MLB pipeline props with server-side publishability rules applied. '
    'App reads from this view instead of applying tier/signal filters '
    'client-side. Threshold changes = one SQL edit, no App Store ship.';

NOTIFY pgrst, 'reload schema';
