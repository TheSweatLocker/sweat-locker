---
name: project_engine_calibration_discussion_1117
description: "Andy 2026-11-17 queued the engine itself for DISCUSSION and rework — measured: our projections are LESS accurate than the market line in 5 of 5 sports, the deviation we bet on is noise or backwards, and conviction inverts at the top (NCAAF rl:STRONG -23.6% ROI)"
metadata:
  node_type: memory
  type: project
---

Andy 2026-11-17, after the NCAAB conviction outage: *"for conviction the
price vs implied probability, this warrants a discussion. Something is
fucked up though and our engine never calibrating and not being good
enough is the issue, no more excuses i want a hard look at what is
driving selection and what is happening on a bigger picture level then we
need to rework as a whole."*

He is right, and the measurements below are the hard look. **Discussion
item — no rework landed unasked.**

---

## 1. The headline: we lose to the prices we bet into

Every graded `jerry_reads` pick carrying BOTH a conviction and a real
price, 2026-10-01..11-16, **n=1234**. Edge = our hit rate minus the
market's implied probability at the price we took.

| | n | our hit% | implied% | edge |
|---|---|---|---|---|
| **OVERALL** | **1234** | **53.2%** | **54.8%** | **−1.6 pp** |

### It is a SPREAD problem

| market | n | hit% | implied% | edge |
|---|---|---|---|---|
| **spread** | **600** | **48.5%** | 52.0% | **−3.5** |
| ml | 530 | 58.5% | 58.1% | +0.4 |
| total | 86 | 51.2% | 52.1% | −0.9 |

Moneylines are flat and totals are flat. **The whole deficit is spreads**,
which is also the market we publish most.

### By sport

| sport | n | hit% | implied% | edge |
|---|---|---|---|---|
| NCAAF | 376 | 54.0% | 55.7% | −1.7 |
| NHL | 314 | 55.1% | 55.5% | −0.4 |
| **NCAAB** | 210 | 51.0% | 53.9% | **−3.0** |
| **NBA** | 187 | 49.7% | 52.9% | **−3.1** |
| NFL | 98 | 53.1% | 54.1% | −1.1 |
| MLB | 31 | 54.8% | 51.7% | +3.1 |

---

## 2. Conviction is anti-predictive where we bet hardest

Same n=1234, bucketed by conviction:

| conviction | n | hit% | implied% | edge |
|---|---|---|---|---|
| **0–44** | 117 | 54.7% | 51.6% | **+3.1** |
| 45–49 | 20 | 40.0% | 50.8% | −10.8 *(n<30)* |
| **50–54** | 447 | 48.5% | 52.4% | **−3.8** |
| 55–59 | 274 | 56.6% | 55.6% | +0.9 |
| 60–64 | 169 | 58.0% | 58.6% | −0.6 |
| 65–69 | 61 | 59.0% | 58.8% | +0.2 |
| **70–100** | 146 | 54.1% | 57.9% | **−3.8** |

**The only clearly positive bucket is the LOWEST.** The highest bucket is
tied for the worst. The middle is market-neutral. The one sport with
n≥30 at 70+ is NCAAF: **n=118, 50.0% against 56.7% implied, −6.7 pp.**

`set_potd_manual.py`'s own docstring already said this in October:
*"Conviction is MEASURED to be a useless ranker: on NCAAF spreads it is
non-monotonic and the 65-74 bucket is the WORST of all at 35.3% n=51."*
It was treated as a POTD-selection caveat. It is an engine-wide fact.

---

## 3. The root: our numbers are worse than the lines they bet against

From `assess_engine_all_markets.py` (temporal train/test split, real
prices, TEST slice only). MAE = mean absolute error of the projection
against the actual result; "deviation slope" = whether our distance from
the market line predicts anything.

| sport · market | our MAE | market MAE | verdict | deviation slope |
|---|---|---|---|---|
| NCAAF spread | 12.629 | 12.116 | **WORSE** | −0.087, CI [−0.479, +0.349] → noise |
| NCAAF total | 13.780 | 12.946 | **WORSE** | −0.334, CI [−0.829, +0.168] → noise |
| NFL spread | 11.482 | 8.133 | **WORSE** | **−0.536, CI [−1.216, −0.207] → BACKWARDS** |
| NFL total | 10.559 | 9.392 | **WORSE** | −0.129, CI [−0.722, +0.410] → noise |
| NHL total | 1.897 | 1.870 | **WORSE** | +0.272, CI [−0.560, +1.159] → noise |

**Five for five, our projection is less accurate than the market's own
line.** And the quantity we actually bet — the gap between our number and
the line — is noise in four of five and **statistically backwards in NFL
spreads**, where the confidence interval excludes zero.

That is the bigger-picture answer. Selection is driven by a projection
that is worse than the number it disagrees with, so a gap between the two
is more likely to be our error than the market's.

### Dumb constants beat us

| control | record | ROI |
|---|---|---|
| NCAAF always UNDER | 167-138, 54.8% | **+4.5%** |
| NCAAF follow our projected_total | 135-170, 44.3% | **−15.5%** |
| NHL ML market favourite | 87-55, 61.3% | +0.7% |
| NHL ML our published picks | 50-33, 60.2% | +0.9% |

On NCAAF totals a one-word rule beats our model by ~20 points of ROI. On
NHL moneylines our published picks match blanket chalk, which is the
measured form of *the engine scores winners, not prices*
([[feedback_the_engine_scores_winners_not_prices]]).

### The tiers invert on spreads

NCAAF ATS, TEST slice:

| tier | record | hit% | ROI | n |
|---|---|---|---|---|
| **rl:STRONG** | 14-21 | **40.0%** | **−23.6%** | 35 |
| rl:LEAN | 27-29 | 48.2% | −8.0% | 56 |
| **rl:PASS** | 18-14 | **56.2%** | **+7.4%** | 32 |
| **rl:COVERAGE** | 9-7 | 56.2% | +7.4% | 16 |
| published, all tiers | 68-71 | 48.9% | −6.6% | 139 |

**The games we PASS on outperform the ones we publish, and our strongest
spread tier is the worst thing we produce.**

---

## 4. Why this keeps presenting as "conviction is broken every day"

`conviction` has **90 scripts that write it** and 7 more that clamp,
scale, cap or shadow it (`clamp_conviction_to_tier`, `conviction_scale`,
`conviction_band_gate`, `conviction_calibration_pass`, `ncaab_conviction`,
`stamp_conviction_rank`, `promote_playbook_conviction`). On 11-17 alone
that one number was absent from the preliminary ctx shape, written NULL by
the bridge, pinned to 0 by the COVERAGE tier cap, and **scaled from 53 to
70** by `conviction_scale` so the POTD threshold would pass. Five live
gates read it, each assuming a different meaning
([[feedback_a_null_never_reaches_a_gate]]).

So there are two separate problems and they have been getting confused:

1. **Plumbing** — one column, no owner, 97 places that can change its
   meaning. This produces the daily breakage.
2. **Substance** — the number has no measured relationship to edge, and
   the projections underneath it are worse than the market. **This is the
   one Andy is calling out, and no amount of plumbing fixes it.**

---

## 5. What a rework has to answer (the discussion)

- **Do we have any edge to sell?** The honest reading of §1–§3 is: not on
  spreads, not on totals, and on moneylines we are selling favourites.
  The positives are small-n or are controls. What is the product if the
  engine does not beat the close?
- **Should we bet against our own tiers?** rl:PASS and the lowest
  conviction bucket are the only positives with n≥30. That is either a
  real inversion worth harvesting or a selection artefact, and it is
  testable.
- **Should the projections be replaced by market-anchored models?** If our
  MAE cannot beat the closing line, the line should probably BE the
  baseline and the model should only predict the *residual*, with the
  burden of proof on it.
- **Is the calibrated shadow the exit?** `ncaab_conviction` (11-13) is a
  real probability, sitting under `_cal_*` on **130 of 621** NCAAB picks
  and deliberately unpromoted because five gates encode the old meaning.
  Measure it as a ranker before promoting anything.
- **What gets switched OFF while this is unresolved?** Spreads are
  −3.5 pp on n=600. There is a case for publishing no spread at all until
  a model earns it.

## 6. What is NOT yet measured

- **NBA and NCAAB are absent from `assess_engine_all_markets` output** —
  the two sports now carrying the slate, and the two worst in §1
  (−3.1 and −3.0). They need the same MAE / deviation-slope treatment
  before any rework decision.
- Everything above pools tiers across the window; no per-tier temporal
  stability check yet.
- UFC (n=18) and MLB (n=31) are below the n≥30 gate for most cuts.
