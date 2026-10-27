# The grading chain, surface by surface

Andy, 2026-10-27: "we will go through every single process of how a pick is
picked, recorded and graded for each surface. Make a list to go down and we
will check every single one."

This is that list. Every number here came from a direct query, re-derived
from final scores rather than read back from the column being audited.

Status: **CHECKED** = re-graded independently and it agrees. **BROKEN** =
defect confirmed. **UNVERIFIABLE** = the chain cannot be checked from
scores alone and needs its own harness.

---

## The one fix already shipped today

`public_receipts` took **zero writes for two days** — 10-25 and 10-26, every
surface — because migration `20261025a` swapped the full unique constraint
for a **partial** unique index, and PostgREST's `on_conflict=` cannot name
an index predicate. Every write returned `400 42P10`. `capture()` is
fail-soft, so the cards published and the only trace was a log line.

Fixed in `3971bec7`: first-write-wins now runs in `capture()` itself.
11 receipts recovered for 10-26 and 10-27.

**My bug, shipped 10-25.** The verify block I wrote checked that the index
existed and that no duplicates existed. It never attempted an upsert.

---

## The list

| # | surface | composer | recorder | grader | status |
|---|---|---|---|---|---|
| 1 | `sweat_card` | `generate_sweat_card.py` | `public_receipt.sweat_card_rows` | `grade_public_receipts.py` | **BROKEN** — 30 finished games ungraded |
| 2 | `sharp_card` | `generate_sharp_card.py` | `public_receipt.sharp_card_rows` | `grade_public_receipts.py` | **BROKEN** — 23 finished games ungraded |
| 3 | `potd` | `jerry_anchor_potd.py` | `public_receipt.potd_rows` | `grade_potd.py`, `reconcile_potd_grades.py` | **BROKEN** — 4 result spellings, 2 denominators |
| 4 | `dawg` | sweat card `dawg` slot | `public_receipt.dawg_rows` | `grade_public_receipts.py` | **BROKEN** — slot empty 14/14 days |
| 5 | `game_read` | `jerry_reads` | `backfill_public_receipts.py` | `grade_jerry_reads.py`, `grade_game_market_receipts.py` | **BROKEN** — 19 confirmed mis-grades |
| 6 | `prop_jerry` | `prop_jerry_reads` | `backfill_public_receipts.py` | `settle_prop_receipts.py`, `grade_prop_jerry_reads.py` | **BROKEN** — 672 finished ungraded, 47% dead |
| 7 | `fade` | `flip_football_spreads.py` | `line_snapshot` | `grade_public_receipts.py` | **CHECKED** — 52 of 52 agree |
| 8 | `ledger` | `ledger_snapshots` | `backfill_public_receipts.py` | `grade_ledger_snapshots.py` | **UNVERIFIABLE** — parlay legs |
| 9 | `daily_degen` | `daily_degen` | `backfill_public_receipts.py` | `grade_daily_card.py` | **UNVERIFIABLE** — parlay legs |

## Health of every surface, measured

Re-graded from final scores. `UNGR-done` = no result stored **although the
game has a final score** — the single most important column here.

```
surface        rows  graded    dead   UNGR  UNGR-done   AGREE  DISAGREE
prop_jerry    17087    8411    7970    706        672       0         0
game_read      1813    1595     184     34          4    1399        19
sweat_card     1127    1056      30     41         30      19         0
sharp_card      735     695      11     29         23      45         0
ledger          299     298       1      0          0       0         0
daily_degen     168     166       0      2          0       0         0
potd            166     147      18      1          1       0         0
dawg            132     128       4      0          0       0         0
fade             54      52       2      0          0      52         0
ALL           21581   12548    8220    813        730    1515        19
```

Independently re-gradable: 1,534 rows → 1,515 agree, **19 wrong (1.24%)**.

Two things that table says loudly:

* **730 receipts have no result on a game that has finished.** 672 of them
  are props.
* **8,220 of 21,581 receipts (38%) are void / no_action** and count toward
  nothing.

## G1 — 19 confirmed mis-grades in `game_read`

Each joins the correct game on the correct date, so none is a join artefact:

```
08-26  Under 9.0    HOU@NYY  3-9  = 12 runs   stored WIN   truth LOSS
08-26  Under 9.0    COL@WSH 13-1  = 14 runs   stored WIN   truth LOSS
08-26  Under 7.0    CIN@SF  10-9  = 19 runs   stored WIN   truth LOSS
08-25  STL ML home  BAL 13, STL 1            stored WIN   truth LOSS
08-23  CIN +1.5 away CIN lost by 2           stored WIN   truth LOSS
09-19  Arizona State -5.5  lost outright     stored WIN   truth LOSS
10-10  Louisiana ML  lost 31-32              stored WIN   truth LOSS
09-26  New Mexico -11.5  won by 24           stored LOSS  truth WIN
09-05  Kentucky -23.5    won by 32           stored LOSS  truth WIN
08-23  Under 8.5    total 4                  stored LOSS  truth WIN
08-26  Under 8.5    total 7                  stored LOSS  truth WIN
```

**8 wrong in our favour, 4 against: net +4 wins we did not earn.** The bias
is the problem, not the count.

## G2 — NFL receipts cannot be joined to NFL results by ANY key

```
receipt         game_id = '4853610e63c246f2027a6a248e642c9a'   matchup = None
nfl_game_results game_id = '20261026_DAL_PHI'  away='DAL' home='PHI'
```

A 32-char hash against a readable key, full team names against abbreviations,
and `matchup` NULL on 2 of 3 rows. So **no NFL receipt is verifiable from the
results table**, and the NFL grader must reach the score some other way. NFL
accuracy is a blind spot, not a clean bill of health. Same family as
`project_nfl_game_id_mismatch_911`.

## G3 — 7,036 receipts (33%) join no game at all

Plus 16 that join a game on a **different date** (NCAAF off-by-one on
09-10/09-11, MLB 08-05 receipts pointing at 08-07 games).

## G4 — `anytime_td_over` has never graded a row

430 receipts, 430 `NO_ACTION`, 0 W/L, all history. 13 of the 19 NFL
`NO_ACTION` props on 10-26. With the inverted conviction from 10/22, stop
publishing the family rather than wire a grader.

## G5 — `actual_value` is empty where it matters

0 of 1,416 prop receipts in 14 days, against 24.2% filled historically.
`game_read` 0 of 1,360 ever. A receipt that cannot show the number that
decided it is the brand promise failing on 67% of our volume.

## G6 — `result` has four spellings in one column

`WIN`/`Win`, `LOSS`/`Loss`, `VOID`/`Void`, `NO PLAY`/`NO_ACTION`. Any
downstream `== 'Win'` silently drops rows, which is why `surface=potd`
(9-10, 47.4%) and `market=potd` (19-15, 55.9%) disagree about the same month.

## G7 — `pick_line` holds two conventions

174 rl receipts store a magnitude, 258 store a signed line, 15 have a label
reading `-12.5` beside a stored `+12.5`. No single formula grades the column;
this broke my own audit grader before it broke anything else. Parse the
signed number out of `pick_label`.

---

## Order of work

1. **G1** — correct the 19 mis-grades. Net +4 in our favour, so it must go
   out whatever else waits.
2. **G2** — give NFL receipts a joinable key. Nothing about NFL grading can
   be trusted or even measured until this exists.
3. **G6** — normalise `result` to Title case at the writer, one spelling.
4. **prop grading** — 672 finished games ungraded and 47% of prop receipts
   dead. Biggest single block of missing record.
5. **G4** — stop publishing `anytime_td_over`.
6. **G5** — restore `actual_value` on props.
7. **G7** — one convention for `pick_line`, with a backfill.
8. **G3** — attach the 7,036 orphans or mark them unverifiable.
