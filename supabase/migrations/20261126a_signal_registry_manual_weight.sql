-- 20261126a · signal_registry manual weight override
--
-- Andy 2026-11-26: "hand wigthing stats absed on previous matchups is legit
-- data analytics" -- correcting me after I said hand-setting a weight "does
-- nothing". He was right, and I had conflated two things.
--
-- HE IS RIGHT ON THE ANALYTICS. Matchup history is the best-measured family
-- in this repo. From signal_registry today:
--
--     MLB pitcher_vs_team_dominant_history   63.09%  n=401  +12.16pp  w 0.89
--     MLB pitcher_vs_team_recent_hit_hard    63.54%  n=384  +14.01pp  w 0.93
--     MLB pitcher_vs_team_ip_below_outs_line 63.77%  n=276  +12.50pp  w 0.95
--     MLB pitcher_vs_team_tagged_history     61.78%  n=259  +13.12pp  w 0.78
--     MLB pitcher_vs_team_owned              69.70%  n= 99  +20.16pp  w 0.99
--
-- Fifteen pitcher_vs_team_* signals, VALIDATED, near-max weight. Weighting on
-- previous matchups was never the thing in question.
--
-- WHAT I ACTUALLY MEANT, and it is narrow: `recommended_weight` is not wired
-- to anything. ensemble_scorer.py names it ONCE, at line 825, inside the
-- SELECT string -- it loads the value and never reads it. The one function
-- that does read it, signal_registry_lookup.weight_of(), is called NOWHERE
-- (its only other mention is the usage example in its own docstring).
-- refit_signal_registry._recommended_weight_for says so out loud: "ensemble
-- scorer's edge_weight() is the runtime authority -- this is a hint." And
-- 20261124b, nine days ago, said the same thing in passing: "This is the same
-- shape as recommended_weight: computed correctly, written where nothing
-- reads it."
--
-- So editing that column changes what every audit and report SAYS about a
-- signal while changing zero picks. That is a broken lever, not an argument
-- against pulling it. This migration builds the lever.
--
-- WHY NOT JUST WIRE recommended_weight UP. Two reasons.
--
--   1. It is a COMPUTED column -- refit_signal_registry line 1416 writes it
--      on every run from _recommended_weight_for(n, hit_rate, tier, market,
--      blind). A hand-set value survives until the next nightly refit. 24
--      files in mlb_pipeline/ write this column.
--   2. Overloading it would destroy the only honest reading of the gap. With
--      a separate column, recommended_weight keeps meaning "what the
--      measurement says" and manual_weight means "what Andy says", and the
--      distance between them is visible and auditable. Widening one column
--      to carry a second model is a defect this repo has already paid for
--      (feedback_a_derived_column_can_hold_two_models), and it is the same
--      call made on 11-25 for the partial price bar, which feeds the tier
--      and is deliberately NOT stored in price_breakeven_pct.
--
-- WHY A TRIGGER AND NOT A PATCH TO THE REFIT. Because fixing one writer is
-- the documented failure here (feedback_fix_every_writer_to_the_table,
-- feedback_guard_belongs_at_the_chokepoint). These 14 files write
-- recommended_weight today:
--
--     refit_signal_registry        apply_signal_weight_discipline
--     backfill_signal_tiers        cap_unmeasured_signal_weight
--     backfill_prop_signal_tiers   repair_uncapped_signal_weights
--     nhl_backfill_signal_tiers    calibrate_signal_weights
--     refresh_prop_signal_calibration  rescore_signal_registry
--     set_ncaab_signal_baseline    backtest_oddscrowd_fade_signals
--     audit_prop_signals           fix_nba_b2b_signal_columns
--
-- A guard in the refit alone leaves thirteen. A guard in all fourteen leaves
-- the fifteenth, written next month. The trigger is the chokepoint and it
-- covers writers that do not exist yet.
--
-- HOW THE TRIGGER TELLS A DELIBERATE OVERRIDE FROM A BULK WRITE. A real
-- override always advances weight_set_at; no bulk writer knows the column
-- exists. So: if weight_set_at is unchanged, the five override columns are
-- restored from OLD. One condition, and it closes the two ways a bulk write
-- could reach them --
--
--   * a PostgREST batch upsert UNIONS the keys across its objects and sends
--     NULL for an object missing one, so one row carrying manual_weight
--     would null it on every other row in the batch
--     (feedback_postgrest_batch_normalize_keys);
--   * a writer that does select=* and round-trips the row would carry the
--     stale value back over a newer one.
--
-- It PRESERVES rather than REJECTS, which matters: hit_rate, sample_n, tier,
-- price_breakeven_pct and baseline_pct keep refreshing nightly underneath a
-- pinned weight. The measurement stays live BESIDE the override, which is
-- what makes the audit possible -- a rejected UPDATE would have frozen the
-- whole row and hidden the very drift we need to see
-- (feedback_a_refused_upsert_freezes_a_lie).
--
-- WHAT THIS DOES NOT OVERRIDE, deliberately:
--
--   * ANTI_VALIDATED still hard-zeros in edge_weight. A manual weight is for
--     a signal the measurement cannot SEE yet -- Andy's actual case is NCAAB
--     h2h at n<=7 sitting on the 0.20 unmeasured floor -- not for
--     resurrecting one measured to be actively wrong. Letting a hand number
--     outrank an ANTI_VALIDATED measurement is exactly
--     feedback_unmeasured_signal_must_not_outweigh_measured, which has cost
--     money here before.
--   * The FADE opinion does not inherit it, for the same reason it does not
--     inherit price_breakeven_pct: the fade bets the other side, so a weight
--     set for the signal is not a weight for its inverse
--     (feedback_check_which_side_a_column_indexes).
--
-- AND IT EXPIRES. weight_review_at stops the override applying after that
-- date, falling back to the measured weight. An override with no expiry is
-- immortal, and this repo has a dead value keyed on itself in its history
-- (feedback_a_dead_value_keyed_on_itself_is_immortal,
-- feedback_a_deferral_needs_a_retry_window). The CLI defaults it to +60 days.
--
-- READER: ensemble_scorer behind ENSEMBLE_MANUAL_WEIGHT (default on) with a
-- probe-guarded select. The probe is not optional -- naming an unknown column
-- in a PostgREST select is a 400, fetch_all_rows answers a 400 with the rows
-- it has (NONE), an empty registry does not error, and every signal in six
-- sports then falls to the 0.20 UNMEASURED_FLOOR
-- (feedback_zero_rows_is_a_wiring_error,
-- feedback_fallback_select_must_degrade_per_column).
--
-- ROLLBACK: ENSEMBLE_MANUAL_WEIGHT=0 switches the reader off and no pick
-- changes. DROP TRIGGER trg_preserve_manual_signal_weight ON
-- public.signal_registry; restores the old clobber behaviour. The columns are
-- additive and safe to leave in place.
--
-- No NOTIFY pgrst: it makes Andy's SQL client report 42P01 relation "a", and
-- Supabase ships a DDL event trigger that reloads the schema itself
-- (feedback_notify_pgrst_breaks_andys_sql_client). No BEGIN/COMMIT either --
-- every statement below is independently idempotent, so a partial run is
-- safe, lines can be pasted one at a time to bisect, and a trailing failure
-- cannot roll back good work.

ALTER TABLE public.signal_registry
  ADD COLUMN IF NOT EXISTS manual_weight    DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS weight_source    TEXT,
  ADD COLUMN IF NOT EXISTS weight_note      TEXT,
  ADD COLUMN IF NOT EXISTS weight_set_at    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS weight_set_by    TEXT,
  ADD COLUMN IF NOT EXISTS weight_review_at DATE;

COMMENT ON COLUMN public.signal_registry.manual_weight IS
  'Hand-set ensemble weight as a FRACTION in [0,1] -- the same scale '
  'edge_weight() returns, NOT a percent and NOT a multiplier. Read by '
  'ensemble_scorer only when weight_source = ''manual'', the row is not '
  'ANTI_VALIDATED, and weight_review_at is absent or not yet past. NULL '
  'means the measured weight applies. Set it with set_signal_weight.py, '
  'never by hand in SQL -- the CLI is what advances weight_set_at, and the '
  'trg_preserve_manual_signal_weight trigger ignores any change to this '
  'column that does not. 20261126a.';

COMMENT ON COLUMN public.signal_registry.weight_source IS
  '''manual'' = manual_weight overrides the measured weight for this row. '
  'NULL or ''measured'' = ensemble_scorer.edge_weight() decides, which is '
  'the default and the behaviour before 20261126a. Nothing else is a valid '
  'value. Note this does NOT affect recommended_weight, which continues to '
  'hold what the MEASUREMENT says so the gap between the two stays visible '
  'to audit_manual_signal_weights.py. 20261126a.';

COMMENT ON COLUMN public.signal_registry.weight_note IS
  'Why this weight was set by hand -- the analytical basis, e.g. which '
  'matchup sample it rests on. Required by set_signal_weight.py: an '
  'override with no stated reason cannot be reviewed, and this column is '
  'what audit_manual_signal_weights.py prints beside the measurement so a '
  'stale rationale is visible rather than inferred. 20261126a.';

COMMENT ON COLUMN public.signal_registry.weight_set_at IS
  'When the override was last written. ALSO THE TRIGGER''S SIGNAL: '
  'trg_preserve_manual_signal_weight restores all six override columns from '
  'OLD whenever an UPDATE leaves this timestamp unchanged, which is how a '
  'deliberate override is told apart from the 14 bulk writers of '
  'recommended_weight and from a PostgREST batch upsert unioning keys. '
  '20261126a.';

COMMENT ON COLUMN public.signal_registry.weight_set_by IS
  'Who set it. Free text, defaults to ''andy'' from the CLI. Present so a '
  'future automated weight-setter cannot be mistaken for a human decision '
  '-- the mistake in feedback_three_writers_three_manual_markers. 20261126a.';

COMMENT ON COLUMN public.signal_registry.weight_review_at IS
  'The override STOPS APPLYING after this date and the measured weight '
  'takes over -- it does not merely warn. Defaults to +60 days from the '
  'CLI. NULL means it never expires, which should be rare and deliberate: '
  'an immortal hand value is how a dead number keeps pricing live picks '
  '(feedback_a_dead_value_keyed_on_itself_is_immortal). 20261126a.';

-- Plausibility guards at the DB, so a bad value cannot reach the scorer even
-- if a future writer skips the CLI (feedback_model_output_needs_a_plausibility_guard).
-- DO blocks because ADD CONSTRAINT has no IF NOT EXISTS form.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'signal_registry_manual_weight_range'
                     AND conrelid = 'public.signal_registry'::regclass) THEN
        ALTER TABLE public.signal_registry
          ADD CONSTRAINT signal_registry_manual_weight_range
          CHECK (manual_weight IS NULL
                 OR (manual_weight >= 0.0 AND manual_weight <= 1.0));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'signal_registry_weight_source_valid'
                     AND conrelid = 'public.signal_registry'::regclass) THEN
        ALTER TABLE public.signal_registry
          ADD CONSTRAINT signal_registry_weight_source_valid
          CHECK (weight_source IS NULL
                 OR weight_source IN ('manual', 'measured'));
    END IF;
END $$;

-- A 'manual' source with no number is a half-written override that would
-- read as "overridden" in every audit while changing nothing.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'signal_registry_manual_needs_weight'
                     AND conrelid = 'public.signal_registry'::regclass) THEN
        ALTER TABLE public.signal_registry
          ADD CONSTRAINT signal_registry_manual_needs_weight
          CHECK (weight_source IS DISTINCT FROM 'manual'
                 OR manual_weight IS NOT NULL);
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.preserve_manual_signal_weight()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- A deliberate override ALWAYS advances weight_set_at (set_signal_weight.py
    -- sends it on every write). Any UPDATE that leaves it untouched is a bulk
    -- writer -- one of the 14 that write recommended_weight, a batch upsert
    -- unioning NULL into keys it never meant to send, or a select=* round-trip
    -- -- so the stored decision is restored rather than clobbered.
    --
    -- Everything NOT listed here stays mutable, on purpose: hit_rate,
    -- sample_n, tier, edge_pp, baseline_pct, price_breakeven_pct and
    -- recommended_weight keep refreshing nightly UNDERNEATH a pinned weight.
    -- That is what lets audit_manual_signal_weights.py see the measurement
    -- drift away from the override instead of freezing the whole row.
    IF NEW.weight_set_at IS NOT DISTINCT FROM OLD.weight_set_at THEN
        NEW.manual_weight    := OLD.manual_weight;
        NEW.weight_source    := OLD.weight_source;
        NEW.weight_note      := OLD.weight_note;
        NEW.weight_set_by    := OLD.weight_set_by;
        NEW.weight_review_at := OLD.weight_review_at;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_preserve_manual_signal_weight
  ON public.signal_registry;

CREATE TRIGGER trg_preserve_manual_signal_weight
  BEFORE UPDATE ON public.signal_registry
  FOR EACH ROW
  EXECUTE FUNCTION public.preserve_manual_signal_weight();

-- Finding the overrides has to be cheap: the scorer reads the whole registry
-- anyway, but audit_manual_signal_weights.py and the daily check filter on
-- exactly this predicate.
CREATE INDEX IF NOT EXISTS idx_signal_registry_manual_weight
  ON public.signal_registry (sport, signal_name)
  WHERE weight_source = 'manual';
