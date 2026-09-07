-- 2026-09-07: v_nfl_props_publishable — mirror of v_mlb_props_publishable
-- but with one extra gate to compensate for NFL tier miscalibration.
--
-- Motivation: user audit 9/7 evening. NFL Jerry tab surfaced 450+ prop
-- cards while MLB surfaces ~50. Root cause is two-part:
--
--   1. App fetches nfl_pipeline_props directly (no view); MLB fetches
--      v_mlb_props_publishable which drops SKIP + coverage kills.
--   2. NFL tier calibration is loose in the season's opening weeks
--      (limited historical samples). Today's 476 raw NFL props break
--      down as: STRONG=384 · LIGHT=88 · LEAN=1 · COVERAGE=2 · SKIP=1.
--      80% STRONG is not a real edge distribution — it's the calibrator
--      lacking data to discriminate. Compare MLB today: 96 raw →
--      PRIME=11 · STRONG=16 · LEAN=28 · SKIP=41 (17% STRONG, normal).
--
-- Mirror-MLB rules (identical to v_mlb_props_publishable):
--   - drop rows with _coverage_kill_gate set
--   - keep any non-SKIP OR SKIP-with-Jerry-BACK
--
-- Extra NFL-only rule (compensates for tier inflation):
--   - require the joined prop_jerry_reads row to have call_verdict='BACK'
--     AND conviction >= 60. This filters 476 → ~133 by dropping every
--     PASS/FADE verdict + weak-conviction BACKs. Yields ~8 props/game
--     (matches MLB density of ~3-4/game once season-length compresses
--     the STRONG-tier inflation).
--
-- Product decision (user 9/7): "medium conv >= 60 — mirror MLB, don't
-- invent a new surface."
--
-- Threshold changes = one SQL edit + PGRST reload; no app resubmit.

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
    COALESCE(p.refit_conviction, p.conviction) AS display_conviction,
    -- SKIP-with-Jerry-BACK badge for parity with MLB view (same UI hook)
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
    -- Rule 1: drop COVERAGE stubs (same as MLB view).
    (
        p.signals->>'_coverage_kill_gate' IS NULL
        OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no','')
    )
    -- Rule 2: keep any non-SKIP OR SKIP-with-Jerry-BACK (same as MLB).
    AND (
        p.tier != 'SKIP'
        OR UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
    )
    -- Rule 3 (NFL-only): require Jerry BACK verdict at conviction >= 60.
    -- Compensates for tier miscalibration in early-season NFL — Jerry's
    -- verdict integrates LR + refit + playbook so a BACK + conv60 combo
    -- is the sharpest signal available while tier stays noisy. Tune this
    -- floor via ALTER VIEW as more calibration data lands.
    AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
    AND COALESCE(pj.conviction, 0) >= 60;

COMMENT ON VIEW public.v_nfl_props_publishable IS
    'NFL pipeline props with server-side publishability rules applied. '
    'Mirrors v_mlb_props_publishable + adds Jerry BACK@conv>=60 gate to '
    'compensate for early-season tier miscalibration. Threshold changes = '
    'one SQL edit, no App Store ship.';

NOTIFY pgrst, 'reload schema';
