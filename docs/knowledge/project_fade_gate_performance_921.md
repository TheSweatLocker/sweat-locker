---
name: project_fade_gate_performance_921
description: 2026-09-21 audit of the sharp-money/FADE classification. SHARP_MOVE tags show NO edge; CONSENSUS_CONFIRMED on ML (68.4%) and fading NEUTRAL (62.1%) are the only earners. Football is 0% graded — metrics are MLB-only.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T21:57:16.439Z
---

Andy asked "does the fade gate even work, what are the performance
metrics?" after we added a third money-flow source
([[project_money_flow_sources_921]]). Measured `line_movement_flags`
(1,870 rows) joined to game results.

## Headline: the sharp-money premise is NOT validated

Breakeven at -110 is 52.4%. Wilson 95% CIs, n = graded flags.

    classification                W-L      n    hit%    95% CI    verdict
    ALL FLAGS                  613-571  1184   51.8%   [49-55]   coin flip
    PATTERN_ONLY               267-238   505   52.9%   [49-57]   no edge
    NEUTRAL                     69-113   182   37.9%   [31-45]   FADE EDGE
    CONSENSUS_CONFIRMED        108-62    170   63.5%   [56-70]   FOLLOW EDGE
    SHARP_MOVE_CONFIRMED        54-42     96   56.2%   [46-66]   no edge
    SHARP_MOVE_LEAN             38-54     92   41.3%   [32-52]   no edge (bad)
    PUBLIC_MOVE_CONFIRMED       24-24     48   50.0%   [36-64]   no edge
    CONSENSUS_LEAN              16-13     29   55.2%   [38-72]   thin
    CONSENSUS_TRIPLE_CONFIRMED  18-10     28   64.3%   [46-79]   thin

**The SHARP_MOVE family — the whole point of the money-flow stack — shows
no edge.** SHARP_MOVE_CONFIRMED's CI straddles chance; SHARP_MOVE_LEAN is
consistently BELOW 50 across every market (ml 38.5, total 42.9, rl 45.5).

What actually earns:
  * **CONSENSUS_CONFIRMED on MONEYLINE ONLY: 68.4% [59-76] n=117.**
    Market-split matters — the same tag on totals is 43.8% (n=32). The
    edge is ML-specific; do not apply the tag blanket.
  * **Fading NEUTRAL: 62.1%**, and robust across all three markets
    (ml 37.5 / total 39.2 / rl 36.4 follow-rates, n=182). Coherent story:
    NEUTRAL means a steam/limit pattern fired but the splits did NOT
    confirm it — unconfirmed steam is a trap, so the flagged side is
    systematically wrong.

## The metrics are 98% MLB — football is unmeasured

    sport   flags  graded
    MLB      1378    1159  (84%)
    NCAAF     441      25  (5.7%)
    NFL        51       0  (0%)

Cause: game_id format split. Flags carry hash ids
(`99279326548a5f53...`) while `nfl_game_results` uses `20270110_TEN_HOU`
and `ncaaf_game_results` uses `ncaaf_20260919_Wyoming_Central Michigan`.
NCAAF flags are a MIX (30 slug / 411 hash); NFL is 100% hash so nothing
joins. Flags DO match nfl_game_context (also hash) — it is only the
results table that diverges. See [[project_nfl_game_id_mismatch_911]].

**So for the two sports Andy wants "state of the art", we have zero
evidence the classification works.** Fixing this join is the highest-value
next step on the money-flow stack — it is not new modelling, just a join.

## Bearing on the new source

The TRIPLE_CONFIRMED tiers — precisely what a third source strengthens —
have 16 SHARP + 29 CONSENSUS flags total, mostly ungraded. Adding
fadethepublic cannot be justified by measured performance yet; it is a bet
on the mechanism, not a validated one. Shadow it before letting it move a
published pick, per [[feedback_suppression_gate_needs_shadow]].

## Recommended actions (not yet done)

1. Fix the football flag->results game_id join. Without it NFL/NCAAF
   money-flow is unfalsifiable.
2. Restrict CONSENSUS_CONFIRMED weighting to moneyline.
3. Stop weighting SHARP_MOVE_LEAN as a positive signal (41.3%). Treat as
   drop rather than auto-fade — its CI upper bound (52) doesn't clearly
   clear the fade threshold either.
4. Add NEUTRAL as an explicit fade candidate (62.1%, robust).
5. Update the 2+ source FADE gate to count ANY two live sources — it
   currently counts OC + FR specifically, and OC no longer exists.

Related: [[feedback_sharp_money_discipline_802]],
[[project_sharp_money_fade_808]], [[feedback_sample_size_with_pct]].

## 2026-09-21 follow-up: the football join is fixed, classification is not

`bridge_football_game_ids.py` + migration 20260921d build
`football_game_id_alias`, mapping any historical football game_id (context
hash, legacy hash, slug) to the canonical results id. 604 aliases; football
flags go **0 -> 376 gradeable** (NFL 43, NCAAF 333).

But that only bought the ability to grade. **355 of those 376 are
PATTERN_ONLY** — no splits verdict attached. September classification rate:

    MLB    52%   (535 classified / 482 PATTERN_ONLY)
    NCAAF   5%   (21 / 364)
    NFL     9%   (4 / 39)

So the sharp-money gate STILL cannot be evaluated on football. The next fix
is `classify_line_moves.py` — its splits join (fadereport/cleatz signals ->
flags) is presumably hitting the same id mismatch the results join did.
Football money-flow remains unfalsifiable until that lands.

Early football numbers (thin, do not act on): ALL FOOTBALL 187-189 (49.7%)
[45-55], essentially all PATTERN_ONLY at 49.3%.

## 2026-09-21 second follow-up: classifier fixed, football now classifies

`classify_line_moves` had three defects keeping football at PATTERN_ONLY:

1. **Stale splits voting.** Every splits read took "latest row for this
   game" with no age bound, so a two-day-old capture voted on today's move.
   In a 3-source agreement test one stale dissenter collapses the result to
   SOURCES_SPLIT/PATTERN_ONLY. Acute with OddsCrowd dead — line_snapshot
   still holds 09-19/20 oddscrowd rows that were still being counted. Now
   bounded to 18h, measured **against the flag, not wall-clock now** (the
   first cut used now() and turned 47/47 NFL flags into money%=None on
   backfill).
2. **Id scheme mismatch** — lookups now try the flag's id then its siblings
   from `football_game_id_alias`.
3. **No backfill window** — `fetch_flags` was hardcoded to 24h, so a fix
   could never reach existing flags, and a Tuesday run saw zero football.
   Added `--since-hours`.

Result on contemporaneous (weekend) football:

    NCAAF  22/30 classified  73%  (was 5%)
    NFL     4/10 classified  40%  (was 9%)

**KNOWN LIMIT — historical backfill is not recoverable.**
`fadereport_signals` / `cleatz_signals` keep only the LATEST snapshot per
(game, market); upserts overwrite `fetched_at`. Median |flag - split| gap
for older NFL flags is **216 hours**. p25 is 0.7h, so recent flags are
fine. `public_splits_archive` is the table that keeps history — it is the
right source if real backfill is ever wanted.

So football money-flow will now accumulate a real record going forward.
It still needs several weeks of slates before the per-classification
samples support a verdict; do not read the current football numbers.
