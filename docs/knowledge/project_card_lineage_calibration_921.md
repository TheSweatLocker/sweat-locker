---
name: project_card_lineage_calibration_921
description: "Sweat Card is now ONE cross-sport list ranked by measured hit rate per (sport, source_table, band), not raw conviction. MLB 80+ looked inverted but was Simpson's paradox — DotD/POTD drag the band."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T21:13:39.761Z
---

Shipped 2026-09-21 (commit 12fe3bc4). Supersedes the open question in
[[project_sweat_card_multisport_921]] — Andy chose "it should all just be
one list", UFC excluded.

## Conviction is not comparable across sports OR across surfaces

Measured on 14,407 graded `public_receipts`. NFL conviction runs ~10pp
hotter than MLB for the same realised hit rate (NFL median conviction 60
vs MLB 50), so a raw-conviction merge quietly promotes football.

MLB's 80+ band *looked* broken — 58.2% [55-61] vs 68.1% [65-71] at 70-80,
non-overlapping on ~900 picks each. It is **Simpson's paradox**, not a bad
model. Split by `source_table`:

    MLB 80+ lineage                 W-L      n     hit
    mlb_game_results (ML/total)     66-27    93   71.0%  [61-79]
    mlb_pipeline_props             154-113  267   57.7%
    daily_best_bet_history (POTD)   56-54   110   50.9%
    daily_dawg (DotD)               67-73   140   47.9%

Inside the game-results lineage conviction is cleanly monotone:
55.6 -> 57.9 -> 61.5 -> **71.0**. The band only looked bad because DotD +
POTD supply 250 of its 610 picks and both lose at high conviction.

**Lesson worth keeping: before trusting an aggregate band, split it by
lineage.** A conviction-100 DotD is not the same bet as a conviction-100
total, and pooling them hides both facts.

## What ships

`generate_sweat_card._cross_sport_calibration()` learns hit rates at run
time from public_receipts with hierarchical shrinkage
(cell -> sport+band -> sport -> global, K=25 pseudo-obs). Ranking key is
`(sport, source_table, conviction band)`. Sports with no graded history
(NHL/NBA pre-opener) land on the global base rate, not zero. Falls back to
raw conviction if the query fails.

DotD and POTD get demoted out of the top of the list but keep their own
dedicated card slots, so nothing is lost from the card.

## Two bugs this exposed

1. The prior ranking used a month-based priority weight as the PRIMARY
   sort key, ahead of conviction — it segregated rather than tie-broke.
   Saturday 09-20 shipped 5 NFL picks and ZERO of the 8 MLB picks,
   including a conviction-100 Tigers ML.
2. `unified_top_picks` was 0/N graded on EVERY card, and football card
   picks had never been graded anywhere (22 receipts back to 09-05 at
   result=NULL). Root cause: `grade_public_receipts.py` shipped 09-20 but
   was never wired into a workflow. It graded all 22 correctly (11-10-1)
   on first run — it had simply never been run. Now in
   mlb_grade_overnight.yml. See [[feedback_validate_data_reaches_new_code]].

Client keeps exactly one piece of logic: a fallback concatenating the
legacy arrays for pre-09-21 cached payloads. The emoji->sport reverse
lookup is gone (it could never separate NFL from NCAAF — both are 🏈).
