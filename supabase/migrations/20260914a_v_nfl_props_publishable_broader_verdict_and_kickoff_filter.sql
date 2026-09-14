-- 2026-09-14 · v_nfl_props_publishable widened + kickoff filter
--
-- Two bugs surfaced by Andy on 9/14 Monday afternoon:
--
-- 1) NFL Prop Jerry surface was empty for MNF DEN@KC (18 tiered props
--    including 4 PRIME on nfl_pipeline_props). Root: view required
--    call_verdict='BACK' AND conviction>=60. But generate_prop_jerry_
--    synthesis is writing tier-shaped verdict values ('PRIME','STRONG',
--    'LEAN') for high-conviction picks, not literal 'BACK'. Result on
--    tonight's MNF: 3 BACK rows (none clearing 60) + 15 PRIME/STRONG/
--    LEAN rows (never considered) → 0 publishable. Broaden the verdict
--    gate to accept BACK/PRIME/STRONG/LEAN (the writer's actual
--    high-conviction set) so tonight's tiered props reach the app.
--    LEAN keeps the >=60 conviction floor so noisy LEAN doesn't flood.
--
-- 2) Props for already-played games stayed visible after kickoff.
--    Andy: "props from last night's game shouldn't be there — should be
--    gone and only tonight's MNF props left." Root: view had only
--    game_date filter (=today+8d in app), no per-game kickoff check.
--    Fix: LEFT JOIN nfl_game_context on game_id to pick up kickoff_utc
--    + LEFT JOIN nfl_game_results to detect settled games. Hide rows
--    whose game either kicked off > 15 min ago OR has a final score.
--    Grace window of 15min covers late-arriving live betting props.
--
-- Threshold changes = one SQL edit + PGRST reload; no app resubmit.
-- See 20260907e for the original view.

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
    (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) IN
        ('BACK','PRIME','STRONG','LEAN')) AS is_skip_back,
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
LEFT JOIN nfl_game_context ctx
    ON ctx.game_id = p.game_id
LEFT JOIN nfl_game_results res
    ON res.game_id = p.game_id
WHERE
    -- Rule 1: drop COVERAGE stubs (unchanged from prior view).
    (
        p.signals->>'_coverage_kill_gate' IS NULL
        OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no','')
    )
    -- Rule 2: keep any non-SKIP OR SKIP-with-Jerry-BACK-alike.
    AND (
        p.tier != 'SKIP'
        OR UPPER(COALESCE(pj.call_verdict, '')) IN ('BACK','PRIME','STRONG','LEAN')
    )
    -- Rule 3 (NFL-only) BROADENED: high-conviction Jerry gate. Accept
    -- BACK/PRIME/STRONG as any conviction (they're already flagged as
    -- high-conviction by tier); LEAN still requires >=60 to filter noise.
    AND (
        UPPER(COALESCE(pj.call_verdict, '')) IN ('BACK','PRIME','STRONG')
        OR (UPPER(COALESCE(pj.call_verdict, '')) = 'LEAN'
            AND COALESCE(pj.conviction, 0) >= 60)
    )
    -- Rule 4 (NEW 2026-09-14): hide props for games already played.
    -- kickoff_utc > now - 15min AND no final score. Grace window covers
    -- edge cases like commence_time drift + late-arriving props.
    AND (
        res.home_score IS NULL
        AND (
            ctx.kickoff_utc IS NULL   -- unknown kickoff → keep (fallback to game_date)
            OR ctx.kickoff_utc > (NOW() - INTERVAL '15 minutes')
        )
    );

COMMENT ON VIEW public.v_nfl_props_publishable IS
    'NFL pipeline props publishability. Widened verdict gate 2026-09-14 '
    'to include PRIME/STRONG/LEAN@60 alongside BACK — synth writer overloads '
    'the verdict field with tier-shaped high-conviction values. Also added '
    'kickoff_utc grace filter so post-kickoff/settled-game props auto-hide.';

NOTIFY pgrst, 'reload schema';
