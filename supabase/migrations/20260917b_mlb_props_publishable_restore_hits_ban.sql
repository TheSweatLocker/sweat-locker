-- 2026-09-17b EMERGENCY RESTORE — hits_over ban + LEAN-hits ban.
-- ==================================================================
-- Andy 9/17 PM: 282 hits props publishable today, ALL COVERAGE tier
-- (144 over + 138 under). "Something we said wasn't going to happen
-- again and it is."
--
-- Root cause: migration 20260917a (this morning's emergency UNDER-
-- family ban restore) did a CREATE OR REPLACE VIEW that dropped
-- three prior WHERE clauses from earlier migrations:
--   * 20260915f — hits_over prop_type ban
--   * 20260915e — hits_over + hits_under at LEAN tier ban
--   * 20260915d — tier whitelist restriction to PRIME/STRONG/LEAN
--     (plus SKIP-with-Jerry-BACK)
--
-- CREATE OR REPLACE VIEW is a full replacement — it doesn't merge
-- prior rules. Anyone who touches this view MUST carry forward EVERY
-- rule that came before. Same class of drift that produced the batter-
-- family composer-vs-view split (fixed 9/17 AM with prop_ban_policy.py).
--
-- Restoring all rules in one consolidated view. See structural fix
-- queued in project_prop_publishable_view_drift_917 for the
-- migration-level guardrail against this class of drift.
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
    -- Rule 4b (2026-09-17b RESTORED from 20260915f): hits_over permanent
    -- ban. LR keeps re-promoting hits_over between pipeline runs.
    AND p.prop_type NOT IN (
        'runs_over',        'runs_under',
        'rbis_over',        'rbis_under',
        'total_bases_over', 'total_bases_under',
        'hr_over',          'hr_under',
        'batter_ks_over',   'batter_ks_under',
        'hits_over'
    )
    -- Rule 5 (2026-09-17b RESTORED from 20260915d): tier whitelist.
    -- COVERAGE-tier props flood the render loop with low-conviction
    -- noise — 282 COVERAGE hits props audited 9/17 PM regression.
    -- Only PRIME/STRONG/LEAN publishable (plus the SKIP-with-Jerry-BACK
    -- clause from Rule 2, which is already covered).
    AND (
        p.tier IN ('PRIME','STRONG','LEAN')
        OR (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK')
    )
    -- Rule 6 (2026-09-17b RESTORED from 20260915e): hits_under LEAN ban.
    -- Even with Rule 4b banning hits_over, hits_under at LEAN was a
    -- documented losing pattern.
    AND NOT (p.prop_type IN ('hits_over','hits_under') AND p.tier = 'LEAN');

NOTIFY pgrst, 'reload schema';
