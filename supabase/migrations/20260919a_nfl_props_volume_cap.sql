-- 2026-09-19a — cap NFL publishable props. APP IS CRASHING.
-- ==================================================================
-- Prop Jerry NFL crashes on load. Cause is payload volume, not a bad
-- row: the app queries v_nfl_props_publishable over a rolling 8-day
-- window (app/index.tsx:8111 dateStrategy 'upcoming_week') with NO
-- LIMIT, then merges prop_jerry_reads into every row and renders.
--
-- Measured 2026-09-19, window 09-19..09-27:
--     389 rows   (09-20 alone = 345)
--     tiers: STRONG 193, LEAN 196, PRIME 0
-- MLB for comparison queries ONE day and gets 78 rows and renders fine.
-- NFL is shipping ~5x MLB's payload into the same component.
--
-- TWO CHANGES, both at the view so they take effect without a client
-- release — the app cannot be fixed today, the view can:
--
--   1. Drop LEAN from the NFL tier whitelist. 196 of the 389 rows are
--      LEAN, the lowest actionable tier, and MLB's own prop_lean
--      rollup is 51.6% at -59u over 1,156 picks. Removing them costs
--      nothing we want to defend.
--
--   2. Keep only the top 40 per game_date by display_conviction. A
--      Sunday NFL slate is structurally bigger than an MLB day; an
--      uncapped view will grow again next week without this.
--
-- Result: 389 -> 58 rows. In line with MLB's 78.
--
-- Ordering inside the cap is display_conviction DESC, then conviction
-- DESC, then player_name as a deterministic tiebreak so the same 40
-- survive on every query rather than shuffling between refreshes.
--
-- CREATE OR REPLACE VIEW REPLACES — this carries forward EVERY prior
-- WHERE clause from 20260918c (coverage kill gate, SKIP-with-Jerry-
-- BACK@conv>=60, tier whitelist) per the feedback_publishable_view_drift
-- lesson. The only rule changes are the two above.
--
-- The outer SELECT lists columns explicitly rather than SELECT * so the
-- row_number helper never leaks into the app's payload as a new field.
--
-- NOT ADDRESSED HERE: PRIME count is 0 on NFL, same as MLB today. That
-- is the _playbook_gate_props demotion bug, tracked separately — this
-- migration is purely about stopping the crash.
--
-- ROLLBACK: rerun 20260918c to restore the uncapped view.
-- ==================================================================

CREATE OR REPLACE VIEW public.v_nfl_props_publishable AS
SELECT
    game_id, game_date, player_name, player_team, matchup,
    prop_type, prop_line, direction, tier, conviction, signals,
    book_line, book_over_odds, book_under_odds, result,
    refit_conviction, display_conviction, is_skip_back,
    jerry_verdict, jerry_short_read, jerry_conviction
FROM (
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
        -- 20260918c: PRIME honors raw conviction on display.
        CASE
            WHEN p.tier = 'PRIME' THEN p.conviction
            ELSE COALESCE(p.refit_conviction, p.conviction)
        END AS display_conviction,
        (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK') AS is_skip_back,
        pj.call_verdict AS jerry_verdict,
        pj.short_read   AS jerry_short_read,
        pj.conviction   AS jerry_conviction,
        ROW_NUMBER() OVER (
            PARTITION BY p.game_date
            ORDER BY
                CASE
                    WHEN p.tier = 'PRIME' THEN p.conviction
                    ELSE COALESCE(p.refit_conviction, p.conviction)
                END DESC NULLS LAST,
                p.conviction DESC NULLS LAST,
                p.player_name ASC
        ) AS _rn
    FROM nfl_pipeline_props p
    LEFT JOIN prop_jerry_reads pj
        ON pj.game_id     = p.game_id
       AND pj.player_name = p.player_name
       AND pj.prop_type   = p.prop_type
       AND pj.direction   = p.direction
       AND pj.sport       = 'NFL'
       AND pj.game_date   = p.game_date
    WHERE
        -- Rule 1 (carried from 20260918c): coverage-kill gate.
        (
            p.signals->>'_coverage_kill_gate' IS NULL
            OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no','')
        )
        -- Rule 2 (carried from 20260907e): NFL SKIP needs BACK@conv>=60.
        AND (
            p.tier != 'SKIP'
            OR (
                UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
                AND COALESCE(pj.conviction, 0) >= 60
            )
        )
        -- Rule 3 (CHANGED 20260919a): LEAN dropped from the whitelist.
        AND (
            p.tier IN ('PRIME','STRONG')
            OR (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK')
        )
) ranked
-- Rule 4 (NEW 20260919a): per-slate volume cap.
WHERE ranked._rn <= 40;

NOTIFY pgrst, 'reload schema';
