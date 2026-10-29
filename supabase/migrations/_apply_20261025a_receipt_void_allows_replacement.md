# 2026-10-25a — a voided receipt still occupies its unique key

**Paste `20261025a_receipt_void_allows_replacement.sql` into the Supabase SQL
editor and Run once.** Idempotent (`IF EXISTS` / `IF NOT EXISTS`), safe to
re-run, and read-neutral — no column added, dropped or retyped, no row
modified.

## Why this is still open on 2026-10-28

The migration was written on 10-25 and **never applied**. It is the only
thing blocking the stale-side repair, and the proof is the live failure:

```
$ python reconcile_stale_side_receipts.py --apply
  !! insert failed for read 7743 (409): {"code":"23505","details":"Key
     (sport, surface, game_date, source_id)=(NCAAF, game_read, 2026-10-17,
     7743) already exists."
  ...
  voided 0, replacements written 0, failed 47
  LIVE receipts still on a stale side: 122 (was 179 before any repair)
```

All 47 rows it tried to replace are **already `VOID`**. With the partial
index in place the insert succeeds, because a voided row stops occupying the
key. Without it, the full constraint counts voided rows and the correct side
can never be written.

Nothing was lost by the failed run: the script inserts before it voids, so
`voided 0` means it stopped cleanly rather than voiding a row it could not
replace.

## What applying it unblocks

`reconcile_stale_side_receipts.py --apply` then writes 47 replacements.
Measured scope on the current data — 180 candidates, **133 deliberately left
alone**:

| | |
|---|---|
| **47** | repairable — the read was finalised BEFORE the game started |
| 64 | the MARKET changed, not just the side — reported only, never repaired (B144 / the R3 decision: replacing an ML receipt with a total would record a bet we never showed) |
| 34 | MLB — `mlb_game_context` has no start-time column, so the guard cannot judge them |
| 25 | the read changed AFTER kickoff — the receipt stands, because it is the honest record of what we published |
| 10 | the replacement cannot be graded — not voided |

## It makes our published record WORSE, which is the point

```
frozen receipts   47 VOID            +0.00u
replacements      24 LOSS / 23 WIN   -2.52u
                                     delta  -2.52u
```

A repair that *improved* the record would be the red flag. These are 47 real
plays currently missing from the denominator entirely; grading them is what
the receipts table is for.

## The guard that makes it honest

A receipt is replaced only when the read's `generated_at` precedes the
game's start time — the side being written was on record before anyone could
know the outcome. That is why the 25 reads regenerated after kickoff are left
alone, and why MLB's 34 are skipped rather than guessed.

## Verify after running

The SQL file carries the three verification queries: the old constraint
should be gone, `public_receipts_live_source_uniq` should be present, and the
duplicate-LIVE-receipt check must return zero rows.

Then re-run the repair and expect `voided 47, replacements written 47,
failed 0`:

```
python reconcile_stale_side_receipts.py            # dry run first
python reconcile_stale_side_receipts.py --apply
```

## The structural fix this does NOT address

These receipts exist because a receipt freezes an early read vintage and
`pick_side` / `pick_line` / `pick_label` are immutable by trigger. The repair
cleans up 47 of them; it does not stop the 48th. Preventing it means not
writing a game_read receipt until the read is final (post-lock), which is a
separate change and is not in this migration.
