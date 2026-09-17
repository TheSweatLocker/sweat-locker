-- 2026-09-17 EMERGENCY RESTORE — full ban on 4 batter UNDER families.
-- ==================================================================
-- Rolls back Rule 4/5 change from migration 20260916h. The selective
-- un-ban (STRONG+ tier gate on hr_under / rbis_under / total_bases_under
-- / runs_under) shipped server-side last night, but the v1.0.1 client
-- with the required PROP_TYPE_LABELS + label formatters + client-side
-- filter update hasn't landed on TestFlight yet.
--
-- Testflight v1.0 client falls through to raw uppercase tab chips
-- ("TOTAL_BASES", "RBIS", "HR") and raw prop_type text on every card
-- because it doesn't have the label mapping for these families. Andy
-- 9/17 AM: "Under total bases as POTD ... sick of this not being
-- handled." User-visible regression.
--
-- Restoring Rule 4 to hard-ban all 8 variants (mirrors 20260915c) +
-- dropping Rule 5. When the v1.0.1 client is live on TestFlight,
-- reapply 20260916h to bring back the selective un-ban.
--
-- Composer files re-banned in parallel (generate_prop_jerry_synthesis,
-- generate_sweat_card, generate_sharp_card, generate_daily_degen,
-- jerry_anchor_potd) so nothing produces banned families server-side
-- while this migration is active.
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
    -- Rule 4 (2026-09-17 RESTORED FULL BAN): every batter-family variant
    -- both sides. Restored until v1.0.1 client ships with PROP_TYPE_LABELS
    -- + label formatters. Reapply 20260916h post-launch to bring back
    -- the STRONG+ selective un-ban.
    AND p.prop_type NOT IN (
        'runs_over',        'runs_under',
        'rbis_over',        'rbis_under',
        'total_bases_over', 'total_bases_under',
        'hr_over',          'hr_under',
        'batter_ks_over',   'batter_ks_under'
    );

NOTIFY pgrst, 'reload schema';
