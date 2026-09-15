-- 2026-09-15 EMERGENCY HOTFIX — ban 4 batter families from Prop Jerry.
-- ================================================================
-- Andy hit the live app late 9/15 and Prop Jerry was flooded (~180 rows
-- of runs/rbis/total_bases/hr LEAN-tier LEAKED from an earlier un-ban
-- attempt) → visible lag + crash. Rule: those 4 batter families do NOT
-- surface to users until they've been properly assessed AND the app has
-- category tabs/labels for them. Right now they have neither.
--
-- Adds Rule 4 to v_mlb_props_publishable: hard-drop all 8 prop_type
-- variants (over + under) for the batter families that don't have
-- established app support:
--   runs_over / runs_under
--   rbis_over / rbis_under
--   total_bases_over / total_bases_under
--   hr_over / hr_under
--
-- The pipeline still WRITES these to mlb_pipeline_props for grading /
-- backtest purposes — we just don't SURFACE them. When app support
-- lands (PROP_TYPE_LABELS entries + category tabs in fetchPipelineProps),
-- pull this Rule 4 back out.
--
-- Belt-and-suspenders companion:
--   * generate_prop_jerry_synthesis.py — _MLB_BANNED_PROP_TYPES restored
--     to all 8 variants (was un-banned earlier session, that was the
--     original mistake).
--   * app/index.tsx fetchPipelineProps — client-side filter added for
--     the same 8 variants (defense in depth; will only take effect on
--     next build).
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
    -- Rule 3 (2026-09-11): require L5/L10 stat rows on lower tiers so
    -- every published card has a recent-form chart.
    AND (
        p.tier IN ('PRIME','STRONG')
        OR (
            jsonb_typeof(p.signals->'_stat_last10') = 'array'
            AND jsonb_array_length(p.signals->'_stat_last10') > 0
        )
    )
    -- Rule 4 (2026-09-15): HARD-BAN batter families with no app-side
    -- category tabs. These prop_types have no PROP_TYPE_LABELS entry
    -- and no dedicated label formatter in the Prop Jerry render loop,
    -- so a LEAN-tier flood renders as raw uppercase strings + swamps
    -- the render loop → app crash. Kill at source until app support
    -- lands. When ready to re-enable, remove Rule 4 AND remove the
    -- entries from _MLB_BANNED_PROP_TYPES in generate_prop_jerry_synthesis.
    AND p.prop_type NOT IN (
        'runs_over',        'runs_under',
        'rbis_over',        'rbis_under',
        'total_bases_over', 'total_bases_under',
        'hr_over',          'hr_under'
    );

NOTIFY pgrst, 'reload schema';
