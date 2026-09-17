-- 2026-09-17c — un-ban PRIME + STRONG hits (both sides).
-- ==================================================================
-- Andy 9/17 PM after volume audit: "I thought we were promoting prime
-- hits though this is where i am confused, still dont want prop jerry
-- flooded with 200 hits but if we have some prime ones that we know
-- are performing well why arent we promoting?"
--
-- 30d retro on hits by tier (verified 9/17):
--   hits_over  PRIME : 38-14 (73.1%) · 48% juice traps · net-positive
--                       even at -200 avg (breakeven 66.7%)
--   hits_over  STRONG: 67-45 (59.8%) · 36% juice traps · thin edge
--   hits_over  LEAN  : 22-22 (50.0%) · 77% juice traps · LOSING
--   hits_under PRIME : 18-0  (100 %) · 17% juice traps · elite
--   hits_under STRONG: 3-1   (75.0%) · small n but positive
--   hits_under LEAN  : 16-11 (59.3%) · marginal
--
-- Prior 20260917b banned hits_over across ALL tiers via Rule 4b (the
-- prop_type NOT IN (…) list). That killed the PRIME+STRONG performers
-- along with the LEAN/COVERAGE noise. Too blunt.
--
-- Fix: DROP hits_over from Rule 4b so PRIME and STRONG hits_over
-- survive. Rule 5 (tier whitelist) still blocks COVERAGE hits. Rule 6
-- still blocks LEAN hits. Net: only PRIME/STRONG hits (both sides)
-- publish — the tiers with real edge.
-- ==================================================================

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
    -- Rule 3 (2026-09-11): require L5/L10 stat rows on lower tiers so
    -- every published card has a recent-form chart.
    AND (
        p.tier IN ('PRIME','STRONG')
        OR (
            jsonb_typeof(p.signals->'_stat_last10') = 'array'
            AND jsonb_array_length(p.signals->'_stat_last10') > 0
        )
    )
    -- Rule 4 (2026-09-17): batter-family full ban. Restored until v1.0.1
    -- client ships with PROP_TYPE_LABELS + label formatters. Reapply
    -- 20260916h post-launch to bring back STRONG+ selective un-ban.
    -- NOTE 2026-09-17c: hits_over REMOVED from this ban. Real 30d retro
    -- shows PRIME 73.1% + STRONG 59.8% — profitable at PRIME, marginal
    -- at STRONG. Rules 5 + 6 handle the LEAN/COVERAGE noise.
    AND p.prop_type NOT IN (
        'runs_over',        'runs_under',
        'rbis_over',        'rbis_under',
        'total_bases_over', 'total_bases_under',
        'hr_over',          'hr_under',
        'batter_ks_over',   'batter_ks_under'
    )
    -- Rule 5 (2026-09-17b): tier whitelist — PRIME/STRONG/LEAN only.
    -- COVERAGE flooded the feed with low-conviction noise (282 COVERAGE
    -- hits props 9/17 PM regression). SKIP-with-Jerry-BACK stays live.
    AND (
        p.tier IN ('PRIME','STRONG','LEAN')
        OR (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK')
    )
    -- Rule 6 (2026-09-17b): hits at LEAN documented losing pattern
    -- (hits_over 50.0% at 77% juice traps, hits_under 59.3%). PRIME
    -- and STRONG hits pass through this rule.
    AND NOT (p.prop_type IN ('hits_over','hits_under') AND p.tier = 'LEAN');

NOTIFY pgrst, 'reload schema';
