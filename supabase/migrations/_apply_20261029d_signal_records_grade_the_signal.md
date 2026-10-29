# Apply 20261029d — make the signal tooltip show the SIGNAL's record

One `ADD COLUMN IF NOT EXISTS`, one `CREATE OR REPLACE VIEW`, one `COMMENT`,
one `GRANT`. No NOTIFY, pure ASCII, idempotent, no data written.

## The bug this fixes, and it is user-facing

`signal_attribution.result` is the **pick's** outcome, stamped onto every
signal row that fired on that pick. `v_signal_records` counted it, and that
view is granted to `anon, authenticated` and read by the game card
`SignalsRow` tooltip. So every signal displayed **our own selection record**,
not its own.

Measured on signals with n>=25 both ways — 11 of 34 inflated:

```
  NCAAF COHORT_NCAAF_HEAVY_HOME_DOG    43.8% shown    60.0% real  +16.2pp
  NCAAF home_team_ats_hot_season       64.3%          50.0%       -14.3pp
  NFL   COHORT_NFL_DIV_HOME_COVER      46.9%          33.3%       -13.5pp
  NCAAF COHORT_NCAAF_HEAVY_HOME_FAV    61.7%          50.9%       -10.8pp
  NFL   LR_SHADOW                      53.3%          42.7%       -10.7pp
```

The error is not random — it penalises a signal for **disagreeing** with the
engine, which is the dissenting voice we most want measured honestly.

This was also urgent rather than dormant: the tooltip's fetch used a bare
`supabase` that threw a swallowed ReferenceError, so the records had never
actually rendered. That was repaired, so **the next build would have started
showing these numbers.**

## After applying — run this, it is not optional

```
cd mlb_pipeline && python backfill_signal_was_right.py --apply
```

The view counts only non-NULL `signal_was_right`, so between the migration
and the backfill the tooltip shows **nothing** rather than something wrong.
That is the correct failure direction, but it is not the finished state.
The script refuses to run before the migration, prints the pooled
shown-vs-real shift, and has a `--dry-run` default.

`signal_attribution_grade.py` now writes the column at grade time, and the
nightly runs `--apply --only-null` as a safety net, so this is a one-off.
