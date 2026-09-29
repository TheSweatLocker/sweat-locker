---
name: project_sharp_money_is_a_fade_929
description: "🚨 MEASURED on 2,063 leak-free obs — FOLLOWING sharp money (money% > bets%) LOSES. MLB total 40.2% z=-4.71, MLB spread 43.6% z=-3.38, football total 45.6% z=-2.39. Monotonic in divergence size. It is a FADE signal. Engine carries uncalibrated sharp-FOLLOW signals at weight 1.0."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-29T20:46:15.432Z
---

Andy 2026-09-29: "are you saying we dont record money flow data to recognize
patterns vs results, how do we know if sharp money is even a signal based on real
results?"

## FIRST, A CORRECTION I OWE

I said money flow could not be assessed because `splits_summary` holds one
timestamp. That was wrong and it was the result of looking at the convenient
denormalized field instead of finding the source tables. There is a full time
series:

    public_splits_v2       436,119 rows — snapshot_ts, per source/market/side/metric
    public_splits_archive  103,549 rows — captured_at, per-source divergence

By sport: MLB 290,307 · NCAAF 91,925 · NFL 52,435 · NHL 1,452 · NBA 0 · NCAAB 0.
NFL spans 09-03 → 09-29, NCAAF 08-23 → 09-29.

## THE ANSWER: sharp money IS a signal, in the opposite direction

Method: last snapshot STRICTLY before kickoff (`snapshot_ts < kickoff_utc`),
averaged across sources, back the side whose money%/handle% most exceeds its
bets%. MLB has no start-time column at all, so a 16:00 UTC (noon ET) game-day
cutoff was used as a conservative proxy — that is a stated limitation.

| sport | market | record | hit% | n | z |
|---|---|---|---|---|---|
| MLB | total | 150-223 | **40.2%** | 373 | **-4.71** |
| MLB | spread | 159-206 | **43.6%** | 365 | **-3.38** |
| MLB | ML | 219-216 | 50.3% | 435 | -0.86 |
| football | total | 139-166 | **45.6%** | 305 | **-2.39** |
| NCAAF | total | 115-142 | 44.7% | 257 | **-2.46** |
| football | spread | 159-140 | 53.2% | 299 | +0.27 |
| football | ML | 146-140 | 51.0% | 286 | -0.46 |

**And it is MONOTONIC in divergence size** — MLB: 0-10pp 47.6%, 10-25pp 46.3%,
25-50pp 42.8%, 50pp+ 42.0%. Football 50pp+: 41.7% (z=-2.16). The stronger the
"sharp" signal, the worse following it does.

FADING it: MLB totals 223-150 (59.8%), MLB spread 206-159 (56.4%), football
totals 166-139 (54.4%).

Monotonic + significant + consistent across two independent sports on n=2,063.
This is the best-evidenced finding in the project.

## THE LIVE PROBLEM

signal_registry carries sharp-money FOLLOW signals with weight and no
calibration:

| signal | sport | weight | hit_rate | sample_n |
|---|---|---|---|---|
| `cross_source_sharp_confirmed` | * | **1.0** | None | None |
| `sharp_split_confirmed_ncaaf` | NCAAF | 0.3 | None | **0** |
| `sharp_split_triple_confirmed_ncaaf` | NCAAF | 0.3 | None | **0** |
| `sharp_scenario_match_ncaaf` | NCAAF | 0.3 | None | **0** |
| `sharp_split_confirmed_ncaab` / `_triple_` / `scenario` | NCAAB | 0.3 | None | 0 |
| `oc_sharp_div_ml` | MLB | 0.7 | 60.7% | 28 |

`cross_source_sharp_confirmed` is sport-agnostic, full weight, never graded. The
only FADE variant, `oc_ml_money_gte_80_fade`, is 63.9% on n=36 at weight 1.0 —
consistent with everything above.

Caveat before flipping anything: the table above grades a GENERIC "follow the
larger divergence" rule, not those specific named conditions. The named signals
with n=0 have never been graded at all, which is its own problem and needs no new
evidence to fix — an uncalibrated signal should not carry weight.

## How to apply

1. Zero the weight on every sharp-FOLLOW signal with sample_n = 0. Structural,
   no new evidence needed.
2. Grade the named sharp signals individually against this method before
   inverting any of them.
3. Any new "sharp money" feature must be validated as FOLLOW vs FADE first. The
   product's implicit assumption (follow the money) is measurably backwards on
   totals in both sports.
4. Use `public_splits_v2` with `snapshot_ts < kickoff_utc` for any future study.
   Never `splits_summary` alone — it is one overwritten value
   ([[project_rolling_stats_leak_trap_929]] is the same class).

Related: [[project_fade_gate_performance_921]] (SHARP_MOVE "no edge" — now known
to be negative, not neutral), [[feedback_sharp_money_discipline_802]],
[[project_signal_calibration_gap_929]].
