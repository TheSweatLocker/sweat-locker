-- 2026-09-18c — display_conviction honors RAW conviction for PRIME tier.
-- ==================================================================
-- Andy directive item choice A. 7 real PRIME props in the view today
-- but 5 of them display at conv 30-57 because refit_conviction
-- overrides raw conviction. App sorts by display_conviction desc, so
-- Paul Skenes ks_under (raw conv 88, refit 30) sorts BELOW LEAN
-- props at display 57 → user sees only 2 PRIMEs on Prop Jerry.
--
-- Concrete gap: 7 PRIMEs in view today, only 2 visible to user.
--   Tyler Phillips ha_under   raw=75 refit=75 → display=75 ✓ visible
--   Paul Skenes ha_under      raw=74 refit=74 → display=74 ✓ visible
--   Dylan Cease er_under      raw=73 refit=57 → display=57 ✗ hidden
--   Gerrit Cole er_over       raw=73 refit=46 → display=46 ✗ hidden
--   Tyler Mahle ks_under      raw=75 refit=30 → display=30 ✗ hidden
--   Connor Prielipp ks_under  raw=75 refit=30 → display=30 ✗ hidden
--   Paul Skenes ks_under      raw=88 refit=30 → display=30 ✗ hidden
--
-- Root: display_conviction = COALESCE(refit_conviction, conviction)
-- treats refit as authoritative override. But PRIME tier IS the
-- pipeline's authoritative "we back this strongly" signal. Refit is
-- a second-opinion audit signal. If we've decided PRIME, honoring a
-- lower refit on DISPLAY says "we back this at 30% strength" —
-- undermines the tier itself. Worst of both worlds.
--
-- Fix: for tier=PRIME, display_conviction = raw conviction. Refit
-- still stored on the row for audit and grading; just not the
-- display authority for PRIME picks. STRONG/LEAN keep the existing
-- refit-first formula (they're lower-tier picks where refit's
-- second-opinion carries more weight in display ranking).
--
-- Same fix applied to v_nfl_props_publishable (identical pattern —
-- refit demotes NFL props too).
--
-- CREATE OR REPLACE VIEW replaces the entire view. This migration
-- COPIES FORWARD every WHERE clause from 20260917b (MLB) and
-- 20260907e (NFL) — carrying rules forward is required (see
-- feedback_publishable_view_drift lesson from 9/17).
--
-- ROLLBACK: rerun 20260917b (MLB) and 20260907e (NFL) to restore
-- the COALESCE display formula.
-- ==================================================================

-- ─── MLB ───────────────────────────────────────────────────────────
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
    -- 2026-09-18c: PRIME honors raw conviction on display.
    CASE
        WHEN p.tier = 'PRIME' THEN p.conviction
        ELSE COALESCE(p.refit_conviction, p.conviction)
    END AS display_conviction,
    (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK') AS is_skip_back,
    pj.call_verdict AS jerry_verdict,
    pj.short_read   AS jerry_short_read,
    pj.conviction   AS jerry_conviction
FROM mlb_pipeline_props p
LEFT JOIN prop_jerry_reads pj
    ON pj.game_id     = p.game_id
   AND pj.player_name = p.player_name
   AND pj.prop_type   = p.prop_type
   AND pj.direction   = p.direction
   AND pj.sport       = 'MLB'
   AND pj.game_date   = p.game_date
WHERE
    -- Rule 1 (from 20260903c): drop COVERAGE stubs flagged by kill gate.
    (
        p.signals->>'_coverage_kill_gate' IS NULL
        OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no','')
    )
    -- Rule 2 (from 20260903b): keep non-SKIP OR SKIP-with-Jerry-BACK.
    AND (
        p.tier != 'SKIP'
        OR UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
    )
    -- Rule 3 (from 20260911a): require L5/L10 stat rows on lower tiers.
    AND (
        p.tier IN ('PRIME','STRONG')
        OR (
            jsonb_typeof(p.signals->'_stat_last10') = 'array'
            AND jsonb_array_length(p.signals->'_stat_last10') > 0
        )
    )
    -- Rule 4 (from 20260917a + 20260917b): batter-family + hits_over full ban.
    AND p.prop_type NOT IN (
        'runs_over',        'runs_under',
        'rbis_over',        'rbis_under',
        'total_bases_over', 'total_bases_under',
        'hr_over',          'hr_under',
        'batter_ks_over',   'batter_ks_under',
        'hits_over'
    )
    -- Rule 5 (from 20260915d + 20260917b): tier whitelist.
    AND (
        p.tier IN ('PRIME','STRONG','LEAN')
        OR (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK')
    )
    -- Rule 6 (from 20260915e + 20260917b): hits_under LEAN ban.
    AND NOT (p.prop_type IN ('hits_over','hits_under') AND p.tier = 'LEAN');


-- ─── NFL ───────────────────────────────────────────────────────────
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
    -- 2026-09-18c: PRIME honors raw conviction on display (same as MLB).
    CASE
        WHEN p.tier = 'PRIME' THEN p.conviction
        ELSE COALESCE(p.refit_conviction, p.conviction)
    END AS display_conviction,
    (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK') AS is_skip_back,
    pj.call_verdict AS jerry_verdict,
    pj.short_read   AS jerry_short_read,
    pj.conviction   AS jerry_conviction
FROM nfl_pipeline_props p
LEFT JOIN prop_jerry_reads pj
    ON pj.game_id     = p.game_id
   AND pj.player_name = p.player_name
   AND pj.prop_type   = p.prop_type
   AND pj.direction   = p.direction
   AND pj.sport       = 'NFL'
   AND pj.game_date   = p.game_date
WHERE
    -- Same coverage-kill gate as MLB.
    (
        p.signals->>'_coverage_kill_gate' IS NULL
        OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no','')
    )
    -- NFL BACK@conv>=60 gate (from 20260907e).
    AND (
        p.tier != 'SKIP'
        OR (
            UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
            AND COALESCE(pj.conviction, 0) >= 60
        )
    )
    -- Tier whitelist mirroring MLB.
    AND (
        p.tier IN ('PRIME','STRONG','LEAN')
        OR (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK')
    );

NOTIFY pgrst, 'reload schema';
