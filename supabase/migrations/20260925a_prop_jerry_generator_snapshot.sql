-- 2026-09-25 · Preserve what the generator actually said, before overrides.
--
-- WHY
-- prop_jerry_reads.call_verdict and .conviction are the SHIPPED values, and
-- eight separate scripts mutate them in place after the read is created:
--   apply_refit_verdict_override, apply_fade_type_discipline,
--   collapse_pitcher_thesis_contradictions, collapse_prop_jerry_contradictions,
--   conviction_calibration_pass, jerry_pre_publish_audit,
--   nfl_prop_fade_jerry_pass, grade_prop_jerry_reads
--
-- Nothing records what the generator originally decided. The pre-override take
-- survives only as English prose inside audit_notes, e.g.
--   "NO_REFIT_CAP: refit_conviction unavailable for ha_under — capping
--    conviction at LEAN 55 … Original take: Kumar Rocker · Hits Allowed
--    UNDER 3.5 @ -115"
-- which names the pick but not the tier or the score it was demoted from.
--
-- Consequence, measured 2026-09-25: asking "how did MLB PRIME props do" took
-- six queries and produced a moving answer, because the column being read was
-- post-override and was being reported as the model's tier. It is also why the
-- refit override cannot be evaluated — we can compare its output to results,
-- but never to the decision it replaced.
--
-- WHAT THIS DOES
-- Adds an immutable snapshot of the generator's own call, written once at
-- insert time by generate_prop_jerry_synthesis.py (the single creator of these
-- rows). Every downstream mutator PATCHes named columns, so none of them touch
-- these — the snapshot is protected by construction rather than by discipline.
--
-- call_verdict / conviction keep their current meaning (the shipped values), so
-- nothing downstream needs to change and no reader breaks.
--
-- After this, the question "model said X, we shipped Y, was Y right?" is one
-- query instead of an argument.

ALTER TABLE public.prop_jerry_reads
    ADD COLUMN IF NOT EXISTS generator_verdict    text,
    ADD COLUMN IF NOT EXISTS generator_conviction integer
        CHECK (generator_conviction BETWEEN 0 AND 100);

COMMENT ON COLUMN public.prop_jerry_reads.generator_verdict IS
    'Immutable: the verdict the generator assigned at insert. Never patched by '
    'downstream overrides. Compare against call_verdict to see what a mutator '
    'changed. NULL on rows created before 2026-09-25.';

COMMENT ON COLUMN public.prop_jerry_reads.generator_conviction IS
    'Immutable: generator conviction at insert. Compare against conviction to '
    'measure how far a cap or boost moved it. NULL on rows created before '
    '2026-09-25.';

-- Backfill only what is provably recoverable: a row with no audit_notes was
-- never touched by a mutator, so its current values ARE the generator's.
-- Rows that WERE overridden keep NULL — the original is genuinely unrecoverable
-- and a guess here would be worse than an honest gap, since the whole point of
-- the column is to be trustworthy.
UPDATE public.prop_jerry_reads
   SET generator_verdict    = call_verdict,
       generator_conviction = conviction
 WHERE generator_verdict IS NULL
   AND (audit_notes IS NULL OR btrim(audit_notes) = '')
   AND call_verdict IS NOT NULL;

-- Find rows where an override actually moved the call:
--   SELECT game_date, player_name, prop_type,
--          generator_verdict, call_verdict,
--          generator_conviction, conviction
--     FROM prop_jerry_reads
--    WHERE generator_verdict IS NOT NULL
--      AND (generator_verdict IS DISTINCT FROM call_verdict
--           OR generator_conviction IS DISTINCT FROM conviction);

NOTIFY pgrst, 'reload schema';
