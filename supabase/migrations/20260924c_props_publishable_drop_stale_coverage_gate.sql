-- 2026-09-24c  MLB prop surface was EMPTY. A stale tag was hiding the slate.
--
-- SYMPTOM: v_mlb_props_publishable returned 0 rows for 2026-09-24 on a
-- 1,127-row slate. 09-23 returned 3. 09-22 returned 90. No PRIME props since
-- 09-22, and nothing at all reaching users today.
--
-- WHAT WAS HIDDEN (verified, all priced, all inside the -300..+150 band):
--     Tyler Phillips   er_under   STRONG  conv 80
--     Kumar Rocker     er_under   STRONG  conv 80
--     Ranger Suarez    er_under   STRONG  conv 80
--     Nick Pivetta     er_under   STRONG  conv 80
--     Bryan Woo        outs_over  STRONG  conv 78
--     Paul Skenes      ha_under   LEAN    conv 65
-- 57 props in total today, 68 on 09-23.
--
-- ROOT CAUSE: a transient condition stored as permanent state.
--
-- apply_refit_verdict_override._demote_coverage_tier kills COVERAGE-tier
-- props (a measured -11% ROI drag) by setting tier='SKIP', conviction=0, and
-- stamping signals._coverage_kill_gate='COVERAGE_TIER_UNPUBLISHABLE'. Its
-- docstring says the tag is what makes the function idempotent.
--
-- It is not. The function selects `tier=eq.COVERAGE`, so a row it already
-- demoted to SKIP cannot be selected again. The tier filter alone is the
-- idempotency. The tag adds nothing to it.
--
-- But a later pipeline pass re-scores those rows, and tier/conviction are
-- overwritten while `signals` keeps the tag. So a row that was briefly a
-- coverage stub, then legitimately re-scored to STRONG with conviction 80,
-- carries a permanent publication ban. Proof it is stale: 131 rows today hold
-- the tag while no longer being SKIP, and 59 of them have conviction > 0 —
-- impossible for a demoted row, since demotion forces conviction to 0. All
-- are stamped _refit_override_at = 2026-09-24.
--
-- The collapse is the two sets drifting apart: rows WITHOUT the tag fell
-- 130 -> 16 -> 13 across 09-22/23/24, while rows WITH lookback data fell
-- 220 -> 152 -> 99, until on 09-24 the intersection was empty. Nothing
-- errored. The slate just went quiet.
--
-- THE FIX: stop gating publication on the tag. Rule 1 is redundant, and has
-- been since 2026-08-26 when the demotion started writing SKIP instead of
-- LEAN/55 (the leak that Rule 1 was added to catch). Everything it guards is
-- already guarded:
--     COVERAGE  -> excluded by Rule 5's tier whitelist (PRIME/STRONG/LEAN)
--     SKIP      -> excluded by Rule 2 and Rule 5
-- Verified empirically: with Rule 1 gone, today's 57 survivors contain no
-- COVERAGE and no SKIP, price -225..+135, and none fall outside the
-- documented -300..+150 band.
--
-- The tag itself is KEPT. It is useful history — "this row was once a
-- coverage stub" — and other tooling reads it as a diagnostic. It simply
-- stops being a permanent ban.
--
-- NFL carries the identical Rule 1 but zero NFL rows hold the tag, because
-- _demote_coverage_tier only ever touches mlb_pipeline_props. Fixed here too
-- so the same outage cannot arrive later by a different route.
--
-- Every other rule is reproduced byte-for-byte from the current definitions
-- (MLB: 20260918c, NFL: 20260919a), per feedback_publishable_view_drift — a
-- view replacement has already silently dropped a prior WHERE clause once.
-- Rules retained: MLB 2,3,4,5,6 and NFL 2,3,4,5,6,7.
--
-- VERIFY after applying:
--     select game_date, tier, count(*) from v_mlb_props_publishable
--      where game_date >= '2026-09-23' group by 1,2 order by 1,2;
-- Expect ~57 rows for 09-24 (7 STRONG / 50 LEAN) and ~68 for 09-23, and
-- zero rows with tier in ('COVERAGE','SKIP').

-- ─── MLB ──────────────────────────────────────
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
    -- Rule 2 (from 20260903b): keep non-SKIP OR SKIP-with-Jerry-BACK.
    (
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

-- ─── NFL ──────────────────────────────────────
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
        -- Rule 2 (carried from 20260907e): NFL SKIP needs BACK@conv>=60.
        (
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
