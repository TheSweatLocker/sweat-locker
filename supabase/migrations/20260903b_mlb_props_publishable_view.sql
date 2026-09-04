-- 2026-09-03 MLB Props Publishability View (launch blocker)
-- ================================================================
-- Moves fetchPipelineProps's SKIP-BACK override + coverage_kill_gate
-- filter from client (app/index.tsx:7572-7576) to a SQL view. App
-- becomes a dumb SELECT * FROM view; threshold changes = DB migration,
-- no App Store ship.
--
-- Rules baked in (matches prior client logic):
--   1. Drop rows where signals._coverage_kill_gate = true (COVERAGE
--      stubs, not user-publishable)
--   2. Keep p.tier NOT SKIP OR (SKIP AND Jerry BACKs it) — SKIP with
--      Jerry BACK verdict is a validated override per R-5 audit 2026-08-01
--   3. Refit_conviction merged in (was a second client query)
--   4. display_conviction = COALESCE(refit_conviction, conviction)
--      so ordering is refit-first-then-legacy consistent with client
--   5. is_skip_back flag for UI badge
--
-- App usage: SELECT * FROM v_mlb_props_publishable WHERE game_date = today
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
    -- Refit conviction from same table (already denormalized)
    p.refit_conviction,
    -- Display conviction: prefer refit when present
    COALESCE(p.refit_conviction, p.conviction) AS display_conviction,
    -- Prop Jerry override flag: SKIP that Jerry BACKs
    (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK') AS is_skip_back,
    -- Full Jerry payload for UI badging
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
    -- Rule 1: never publish COVERAGE stubs (internal-only per
    -- apply_refit_verdict_override._demote_coverage_tier)
    COALESCE((p.signals->>'_coverage_kill_gate')::boolean, false) = false
    -- Rule 2: keep any non-SKIP OR SKIP-with-Jerry-BACK
    AND (
        p.tier != 'SKIP'
        OR UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
    );

COMMENT ON VIEW public.v_mlb_props_publishable IS
    'MLB pipeline props with server-side publishability rules applied. '
    'App reads from this view instead of applying tier/signal filters '
    'client-side (per feedback_backside_dictates_app_renders directive). '
    'Threshold changes = one SQL edit, no App Store ship.';

NOTIFY pgrst, 'reload schema';
