-- 2026-09-16 SELECTIVE UN-BAN v2 (project_lr_under_family_unban_913).
-- ==================================================================
-- Third attempt at un-banning the 4 batter UNDER families. Prior
-- 9/15 morning try re-banned same day because tabs never landed +
-- view didn't tier-gate → LEAN flood into the render loop.
--
-- This pass ships the missing pieces together:
--   (a) app: PROP_TYPE_LABELS + label formatters + client filter update
--       (see app/index.tsx tonight's build)
--   (b) composer: generate_prop_jerry_synthesis.py drops the 4 UNDERs
--       from _MLB_BANNED_PROP_TYPES + applies its own STRONG+ tier
--       gate (belt for the suspenders below).
--   (c) view: this migration — Rule 4 restricted to OVER-only variants,
--       new Rule 5 tier-gates the 4 UNDER families to STRONG+.
--   (d) L5/L10 backfill: MLB_STAT_MAP + _MLB_API_STAT already have
--       entries for hr/rbis/runs/total_bases (kept from 9/15).
--
-- Sizing (30d LR retro):
--   hr_under          207 promoted, 149-15,  90.9% hit
--   rbis_under        183 promoted, 104-46,  69.3% hit
--   total_bases_under 217 promoted, 116-66,  63.7% hit
--   runs_under        196 promoted,  98-64,  60.5% hit
--   Total: 467 wins hidden over 30d just from these 4.
--
-- Regression watch: 7-day rolling on the 4 families for first 2 weeks
-- post-launch. If any drops below 55% hit rate, re-add to Rule 4.
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
    -- Rule 4 (2026-09-16 REVISED): OVER-only ban. The 4 batter OVER
    -- families were 30d trainwrecks (hr_over 12.6%, rbis_over 31%,
    -- total_bases_over 32.4%, runs_over 45.7%) — permanent ban.
    -- batter_ks_over/_under stay banned pending dedicated review.
    -- UNDER variants ride on Rule 5's tier gate instead.
    AND p.prop_type NOT IN (
        'runs_over',
        'rbis_over',
        'total_bases_over',
        'hr_over',
        'batter_ks_over',
        'batter_ks_under'
    )
    -- Rule 5 (2026-09-16 NEW): the 4 UNDER families are un-banned but
    -- gated to STRONG/PRIME tier only. LEAN volume floods the render
    -- loop (root cause of 9/15 morning re-ban). LR shadow prints on
    -- these at 60-91% but the STRONG+ subset is where the real edge
    -- is concentrated; LEAN can re-open once tabs mature.
    AND (
        p.prop_type NOT IN (
            'hr_under', 'rbis_under', 'total_bases_under', 'runs_under'
        )
        OR p.tier IN ('PRIME', 'STRONG')
    );

NOTIFY pgrst, 'reload schema';
