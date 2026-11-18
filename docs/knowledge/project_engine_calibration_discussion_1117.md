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

## 7. The conviction root cause, measured (added 2026-11-17 evening)

Andy: *"I want to fix the root cause of conviction being shit."* New
script `audit_conviction_by_engine.py`, **n=1738 graded picks** since
09-01 across all five sports, outcomes taken from each sport's own graded
`home_win` / `spread_result` / `total_result` columns so no spread sign is
derived.

### 7.1 What conviction is, structurally

**One column, four formulas, four different units.** Since 10-01:

| writer | picks | the number *is* |
|---|---|---|
| `ensemble_scorer.py:3208` (`ensemble_v2`) | 1247 | `50 + win_score×10 + margin×20` — signal **agreement** |
| `lr_v1` override | 458 | `p_home_win × 100` — a **probability** |
| `conviction_scale.rank_from_signals/2026-10-30` | 562 | a **percentile rank**, layered on top |
| `ncaab_conviction/2026-11-13` | 153 | a **calibrated probability** |
| *(no `_engine`)* | 223 | the preliminary shape — the NULL class fixed this morning |

The mixture ratio differs **by sport**: NFL is 46% `lr_v1`, NCAAB 0%,
NHL 60%. So "conviction 70" is a different claim in each sport, and the
POTD threshold, the Sweat Card tier floor and The Sharp all read it with
**one** number. That is a real defect and it is the source of the daily
NULL-and-rescale breakage.

### 7.2 But that is NOT why it fails to rank

The cheap hypothesis — *it's a mixture, normalise the units and the gates
work* — had to be tested first, because it is much cheaper than an engine
rework. It is **dead**:

```
_engine=ensemble_v2   n=1233   spearman(conviction, win) = +0.0008  z=+0.03  -> NO RANK
_engine=lr_v1         n= 503   spearman(conviction, win) = +0.0233  z=+0.52  -> NO RANK
whole pool            n=1738   spearman                   = +0.0329  z=+1.37  -> NO RANK
```

`+0.0008` is as close to exactly zero as a measurement gets. **Within a
single formula, on its own scale, conviction carries no information about
whether the pick wins.** Normalising the units fixes the plumbing and
changes nothing about the signal.

### 7.3 Both inputs to the formula are noise

`ensemble_scorer` has carried this comment since 09-29: *"win_score, which
drives `base` (the 50-80 term), is ALSO non-monotonic on the same sample
… conviction still does not rank pick quality and must not be trusted for
stake sizing until `base` has its own study."* **The study is now done.**

| `win_score` | n | hit | vs implied |
|---|---|---|---|
| 0–0.3 | 383 | 50.1% | −4.7 pp |
| 0.3–0.6 | 64 | 54.7% | −4.2 pp |
| **0.6–1.0** | 75 | **45.3%** | −1.8 pp |
| **1.0–1.5** | 42 | **47.6%** | — |

| `margin` | n | hit |
|---|---|---|
| 0–0.1 | 243 | 48.6% |
| 0.1–0.3 | 170 | 53.5% |
| 0.3–0.6 | 59 | 49.2% |
| 0.6+ | 118 | 49.2% |

`win_score` is flat-to-inverse and **below breakeven exactly in the
0.6–1.5 range where the formula pays +6 to +15 points**. `margin` is flat
across all four bands, 48.6–53.5%, nothing to rank with.

**So conviction is a linear function of two measured-noise variables.** It
is not miscalibrated — there is nothing in it to calibrate. That is the
root cause, and it explains why every calibration pass has failed: each
one re-weighted two terms that contain no signal.

### 7.4 The constructive fix also fails *as currently built*

The obvious replacement is to make conviction mean what everyone reads it
as: **our probability for the published side minus the implied probability
at the price taken.** Only `lr_v1` and the NCAAB calibrated model emit a
probability at all, giving n=521 picks with both a probability and a price.

```
spearman(model edge, win) = -0.1518   z=-3.46   n=521   -> INVERSE
```

| model's claimed edge | n | hit | vs implied |
|---|---|---|---|
| ≤ −5 pp | 166 | 68.1% | −1.2 pp |
| −5…0 pp | 181 | 63.5% | +0.7 pp |
| 0…+5 pp | 85 | 55.3% | −3.2 pp |
| +5…+10 pp | 25 | 56.0% | *(under n)* |
| **≥ +10 pp** | 64 | **43.8%** | **−7.8 pp** |

The more edge our model claims, the worse the pick does — monotonically,
at z=−3.46.

### 7.5 The price control, because model vote is a dog proxy

This repo has been fooled twice by exactly this shape
(`project_favourite_bias_was_the_market_chooser_1029`,
`feedback_model_vote_may_be_a_dog_proxy`), so §7.4 does not count until
the price is held fixed. And the proxy **is** real: claim-edge picks
average **55.8%** implied against **65.9%** for concede picks — a 10 pp
longer price.

Holding price roughly fixed, the inversion survives in the pick-dense
middle of the board and vanishes at the top:

| implied band | claim edge | concede price | gap |
|---|---|---|---|
| 45–55% | 47.8% (n=46) | 60.5% (n=38) | **−12.7 pp** |
| 55–65% | 51.4% (n=72) | 57.3% (n=150) | **−5.9 pp** |
| 65–80% | 75.0% (n=32) | 75.4% (n=114) | −0.4 pp |
| 80%+ | *no picks* | 82.1% (n=39) | — |

**Pooled 45–65%, where the price is now within 3 pp:**

```
claim edge      n=118   hit 50.0%   vs imp 56.0%   edge -6.0pp
concede price   n=188   hit 58.0%   vs imp 59.0%   edge -1.0pp
mean implied    claim 56.0%  vs  concede 59.0%
```

So roughly half the raw inversion in §7.4 was the dog proxy, and **half
of it is real**: at the same price, the picks where our model disagrees
with the market hit 50.0% and the ones where it agrees hit 58.0%.


### 7.6 TIER is built from the same noise, and does not rank either

Andy chose *"zero the noise terms now"*. Checking the blast radius first
turned up the thing that makes that change **cosmetic**.

`tier` is not derived from `conviction`. It is promoted independently, by
threshold tests on **`win_score`, `win_classes` and `margin`**
(`ensemble_scorer.py` ~:3021, the `min_score` / `min_classes` /
`min_margin` bars). Those are the same two terms measured as noise in
§7.3 — **plus `win_classes`, which was measured monotonically inverse on
09-29, removed from `conviction`, and never removed from the tier bars.**

Measured the same way, n=1665 with a tier in COVERAGE/LEAN/STRONG/PRIME:

```
spearman(tier_rank, win) = -0.0216   z=-0.88   n=1665   -> NO RANK
```

| tier | n | hit | priced | vs implied |
|---|---|---|---|---|
| COVERAGE | 882 | 55.9% | 669 | **−2.2 pp** |
| LEAN | 397 | 52.9% | 72 | +4.4 pp |
| STRONG | 353 | 55.0% | 151 | −1.1 pp |
| **PRIME** | **33** | **45.5%** | 26 | **−20.6 pp** |

PRIME — the label carrying the most confidence, driving stake sizing and
card inclusion — is the **worst** bucket. Its priced n=26 is under the
n≥30 bar, so read the **direction**, not the figure; the direction matches
the NCAAF `rl:STRONG` 40.0% / −23.6% ROI result in §1. Meanwhile
**COVERAGE**, the bucket that explicitly means *"we cannot assert an
edge"*, is the best of the large buckets on edge.

**Consequence for the chosen fix:** tier is what moves money — stake size,
card inclusion, The Sharp's gate, the PRIME/STRONG language users read.
Zeroing the conviction terms stops the **displayed number** asserting a
ranking while **PRIME/STRONG keep asserting it**. The conviction change is
necessary but not sufficient.

### 7.7 Shadow: what flipping the flag actually does

Implemented as `_CONVICTION_NOISE_TERMS_ZEROED` in `ensemble_scorer.py`,
**default False**, with the measurement in the comment. Shadowed over the
same n=1738 (§8 of `audit_conviction_by_engine.py`):

```
POTD-eligible (conviction >= 70, jerry_anchor_potd.py:950)
  before 316   engines {ensemble_v2: 226, lr_v1: 90}
               sports  {NCAAF 282, NFL 23, NHL 5, NCAAB 4, NBA 2}
  after   90   engines {lr_v1: 90}
               sports  {NCAAF 81, NFL 8, NHL 1}
```

Two blocking consequences, which is why the flag is off:

1. **NBA and NCAAB lose POTD eligibility entirely** — the two sports
   carrying the slate right now. NCAAB has **0% `lr_v1` coverage**, so it
   would never produce a POTD again. The remaining pool is `lr_v1` only,
   whose conviction *is* a win probability, so POTD becomes **heavy
   favourites by construction** — straight into the documented −200+ ML
   trap.
2. **Up to a 76-way tie.** Every `ensemble_v2` pick holds exactly 50, and
   `eligible.sort(key=rank_key)` at :974 then ties them all; 2026-09-12
   would have been a 76-way tie, 11-07 a 74-way, broken by list order
   (`feedback_unordered_data_zero_picks_a_vintage`). Arbitrary selection
   is worse than noisy selection, because it is silent.

So the flip needs a POTD chooser that is not conviction, and the
measurement says **nothing in the ensemble ranks** — so that chooser has
to come from outside it. That is the open decision, not an implementation
detail.

### 7.8 What this settles

1. **Conviction cannot be repaired by re-weighting.** Both of its inputs
   are measured noise. Any formula over `win_score` and `margin` is a
   formula over nothing.
2. **It cannot be repaired by normalising units**, though it should be
   normalised anyway for the plumbing: `spearman +0.0008` inside one
   engine.
3. **It cannot be switched to model-edge either**, because our model's
   disagreement with the market is anti-informative at the same price
   (−8.0 pp on n=118 vs 188).
4. **The only component not losing is `lr_v1`**: −0.2 pp on n=384 priced,
   versus −3.9 pp for `ensemble_v2` on n=540. A model that matches the
   close is what a well-fit model with no edge looks like — which is the
   honest ceiling currently in the building.

### 7.9 What this does NOT license

**Fading ourselves is not a betting rule yet.** n=118 on the claim-edge
side, pooled across five sports, is one study
(`feedback_slope_is_not_a_betting_rule`,
`feedback_unmeasured_signal_must_not_outweigh_measured`). Before any
"fade the model" surface exists it needs: a per-sport split, a temporal
split (does it hold in the second half?), and a sport-neutral control. It
is a finding about **what conviction should be built from**, not a play.
