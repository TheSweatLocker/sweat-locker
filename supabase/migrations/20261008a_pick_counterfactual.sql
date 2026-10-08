-- ════════════════════════════════════════════════════════════════════════
-- 20261008a · Record the pick we DIDN'T publish, and grade it too
--
-- Andy 2026-10-08, after directing the CLE @ CHW switch to White Sox:
--   "Lets log which matchups to saee if the flipp would have been correct"
--
-- WHY THIS IS NOT JUST BOOKKEEPING
-- --------------------------------
-- Two different mechanisms produce a published pick that disagrees with what
-- the engine computed, and today both fired on the same day:
--
--   1. MANUAL  — the CLE @ CHW switch. lr_v1 had Cleveland at p_home 0.274;
--      we published Chicago because the resolver, jerry_pred, projected_spread,
--      the dawg cohort and 2 of 3 externals backed it. Defensible, and
--      currently unmeasurable.
--
--   2. LOCK_DRIFT — `recompute_nfl_primary_play` re-scores after team form,
--      defense and team stats land, and the DB pick lock refuses the write.
--      Measured 2026-10-08: the script wanted to change 24 of 31 NFL games,
--      including 9 side flips (TEN +7.5 -> HOU -7.5, WAS -3.5 -> NYG +3.5,
--      SEA -3 -> SF +3, MIA +7 -> CIN -7, ...). Every refusal lands in
--      pick_lock_drift and nothing has ever graded the side we refused.
--
-- Both cases ask the same question and neither could answer it: was the thing
-- we published better than the thing we discarded? `project_jerry_override_
-- costs_the_edge_1007` measured the override PATH at 49% against the model's
-- 58% — this table is how that gets measured per decision instead of in
-- aggregate, so the answer can eventually change a gate rather than a note.
--
-- DESIGN NOTES
--   * Both sides carry their own odds, because a flip usually changes the
--     price and a units verdict that ignores that is worthless.
--   * `verdict` is DERIVED by the grader, never written by hand.
--   * One row per (game_id, origin, alt_label) so re-running the recorder is
--     idempotent and a repeated nightly refusal does not pile up duplicates.
--   * Nothing here feeds a user-facing record. This is an internal ledger for
--     deciding whether the lock and the overrides earn their keep.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.pick_counterfactual (
    id                 bigserial PRIMARY KEY,
    sport              text        NOT NULL,
    game_date          date        NOT NULL,
    game_id            text        NOT NULL,
    matchup            text,

    -- how the disagreement arose
    origin             text        NOT NULL,   -- 'manual' | 'lock_drift'
    reason             text,

    -- what users actually saw
    published_side     text,
    published_label    text,
    published_market   text,
    published_tier     text,
    published_conv     integer,
    published_odds     integer,
    published_result   text,                   -- WIN/LOSS/PUSH, set by grader

    -- what we did not publish
    alt_side           text,
    alt_label          text,
    alt_market         text,
    alt_tier           text,
    alt_conv           integer,
    alt_odds           integer,
    alt_result         text,                   -- WIN/LOSS/PUSH, set by grader

    -- derived
    verdict            text,                   -- published_better | alt_better
                                               -- | same | undecided
    published_units    numeric,
    alt_units          numeric,

    recorded_at        timestamptz NOT NULL DEFAULT now(),
    graded_at          timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS pick_counterfactual_uniq
    ON public.pick_counterfactual (game_id, origin, COALESCE(alt_label, ''));

CREATE INDEX IF NOT EXISTS pick_counterfactual_ungraded_idx
    ON public.pick_counterfactual (game_date)
    WHERE graded_at IS NULL;

CREATE INDEX IF NOT EXISTS pick_counterfactual_verdict_idx
    ON public.pick_counterfactual (sport, origin, verdict);

ALTER TABLE public.pick_counterfactual ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS pick_counterfactual_service ON public.pick_counterfactual;
CREATE POLICY pick_counterfactual_service ON public.pick_counterfactual
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

COMMENT ON TABLE public.pick_counterfactual IS
    'Internal ledger: for every published pick that disagreed with what the '
    'engine computed (manual override, or a write the pick lock refused), '
    'both sides and both graded results. Answers "was the flip correct?" '
    'per decision. Not user-facing. See 20261008a.';

COMMENT ON COLUMN public.pick_counterfactual.origin IS
    'manual = a human directed a different pick. lock_drift = the engine '
    'tried to rewrite a locked pick and the trigger refused it.';

NOTIFY pgrst, 'reload schema';
