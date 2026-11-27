# Apply 20261126a — a hand-set weight that actually reaches a pick

Six `ADD COLUMN IF NOT EXISTS` on `public.signal_registry`, three CHECK
constraints, one BEFORE UPDATE trigger, one partial index. Idempotent, no
data written, no `NOTIFY`, pure ASCII. Safe to run twice.

Paste the whole file. It has **no BEGIN/COMMIT** on purpose, so a trailing
failure cannot roll back good work and the statements can be pasted one at a
time to bisect.

## Why

Andy, 2026-11-26: *"hand wigthing stats absed on previous matchups is legit
data analytics"* — correcting me after I said hand-setting a weight does
nothing.

He was right about the analytics. Matchup history is the best-measured family
in the repo:

```
MLB pitcher_vs_team_dominant_history   63.09%  n=401  +12.16pp  w 0.89
MLB pitcher_vs_team_recent_hit_hard    63.54%  n=384  +14.01pp  w 0.93
MLB pitcher_vs_team_ip_below_outs_line 63.77%  n=276  +12.50pp  w 0.95
MLB pitcher_vs_team_owned              69.70%  n= 99  +20.16pp  w 0.99
```

What was broken is the lever, not the method. `recommended_weight` is named
**once** in `ensemble_scorer.py` — inside the SELECT at `_load_registry` — and
the value is never read. `signal_registry_lookup.weight_of()`, the one
function that does read it, is called nowhere in the repo. So editing that
column moves every audit number and zero picks. `20261124b` already said so
in passing: *"the same shape as recommended_weight: computed correctly,
written where nothing reads it."*

This adds `manual_weight`, which `ensemble_scorer._manual_weight_of` does
read.

## What it changes about live picks

**Nothing, on its own.** The override requires `weight_source = 'manual'` AND
a non-NULL `manual_weight`, and zero rows carry either until someone runs
`set_signal_weight.py`. The reader ships default-on because it cannot move a
pick until an override exists.

## Verify after pasting

```bash
cd mlb_pipeline

# 1. the columns are live (this is the gate the CLI and the scorer both use)
python audit_manual_signal_weights.py
#    expected: "no manual weight overrides are set" (exit 0)
#    if it still says "absent", the paste did not land

# 2. the scorer sees them and the registry did NOT collapse
python -c "
import ensemble_scorer as E
print('probe:', E._manual_weight_column_exists())
print('rows :', len(E._load_registry()))"
#    expected: probe True, rows ~1850 (NOT 0 -- 0 means every signal in six
#    sports falls to the 0.20 UNMEASURED_FLOOR)

# 3. set one, dry run first
python set_signal_weight.py --sport NCAAB --signal h2h_under_streak \
    --scope total --weight 0.45 \
    --note "3-season h2h: 2,898 of 7,078 pairs have a prior meeting"
python set_signal_weight.py ... --apply      # reads the row back to confirm
```

## If his SQL client reports an error

Check whether the work landed before believing it. The client has reported
`42P01: relation "a" does not exist` on migrations that **fully succeeded** —
it wraps statements to fetch results and chokes on ones that return no rows.
One `select=manual_weight` returning 200 settles it. (This file contains no
`NOTIFY`, which was the usual trigger; Supabase reloads the schema itself via
a DDL event trigger.)

## The trigger, and the one thing to know about it

`trg_preserve_manual_signal_weight` restores all five override columns from
`OLD` whenever an UPDATE leaves `weight_set_at` unchanged.

That is how a deliberate override is told apart from the **14 files** that
write `recommended_weight` (`refit_signal_registry`,
`apply_signal_weight_discipline`, `cap_unmeasured_signal_weight`,
`calibrate_signal_weights`, `repair_uncapped_signal_weights`,
`rescore_signal_registry`, `set_ncaab_signal_baseline`, four `backfill_*`,
`refresh_prop_signal_calibration`, `backtest_oddscrowd_fade_signals`,
`audit_prop_signals`). Guarding the refit alone would leave thirteen;
guarding all fourteen would leave the fifteenth, written next month.

**Consequence:** any hand-written SQL that sets `manual_weight` without also
advancing `weight_set_at` will be silently ignored. Use
`set_signal_weight.py`, which always sends it and reads the row back.

It **preserves** rather than rejects, so `hit_rate`, `sample_n`, `tier`,
`baseline_pct`, `price_breakeven_pct` and `recommended_weight` keep
refreshing nightly underneath a pinned weight — that gap is what
`audit_manual_signal_weights.py` reports.

## Rollback

* `ENSEMBLE_MANUAL_WEIGHT=0` — reader off, no pick changes.
* `DROP TRIGGER trg_preserve_manual_signal_weight ON public.signal_registry;`
  — restores the old clobber behaviour.
* The columns are additive and safe to leave in place.

## Validation run before handover

```
python validate_migration.py ../supabase/migrations/20261126a_signal_registry_manual_weight.sql
  14 statements parse clean, 0 skipped, 0 malformed.
```

**Parse-only, which is strictly weaker than execution** — the DB password is
not in this repo, so the catalog was not consulted. The two catalog-dependent
risks were closed against live precedent instead: `EXECUTE FUNCTION` is used
by 21 existing triggers here (`freeze_receipt_identity` is the same
`BEFORE UPDATE … FOR EACH ROW` shape), and the constraint guards are scoped by
`conrelid` the way `20260904a` does it. PG is 17.6.
