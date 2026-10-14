# UFC v1 Backtest Report

_Generated 2026-08-20T01:05:55Z_  
_Models trained 2026-05-06T21:01:15.327154 (`ufc_v1_*.json`)_  
_Holdout window: `event_date >= 2025-01-01`_  
_True out-of-sample subset: `event_date > 2026-05-06` (post training-run)_

## 0. Methodology caveat (read first)

The shipped `ufc_v1_*` models were trained via **random 80/20 split of ALL fights present at training time**, not a time-based split. Consequently, most of the requested `>= 2025-01-01` holdout was *seen during training* and its accuracy is optimistic. The `> 2026-05-06` subset is the only true out-of-sample slice — treat it as the launch-readiness signal.

## 1. Sample sizes

| Cohort | Fights in DB | Gradeable (features + winner) |
|---|---:|---:|
| Pre-holdout (`< 2025-01-01`) — training window | 328 | — |
| Requested holdout (`>= 2025-01-01`) — partial leakage | 787 | 681 |
| True out-of-sample (`> 2026-05-06`) | 86 | 51 |

## 2. Winner accuracy — headline

**Data-orientation warning.** `ufc_resolve_fights.py` stores fighters in ESPN's returned order, and ESPN lists **winners first** — so `winner='a'` in 98.1% of the leaky holdout and 74.5% of the true OOS. Any accuracy metric computed in the natural DB orientation only is confounded by this. The **symmetric accuracy** row below scores each fight in both orientations and averages — that's the fair number, matching how the model was trained (`_swap_ab` doubling).

| Metric | Requested holdout (leaky) | True OOS |
|---|---:|---:|
| Overall accuracy — natural DB orientation | 84.3% (n=681) | 56.9% (n=51) |
| **Symmetric accuracy** (both orientations avg) | **84.0%** (n=1362) | **54.9%** (n=102) |
| Favorite-side symmetric (nat + swap, conv >= 55%) | 87.5% (n=614) | 54.5% (n=44) |
| Symmetry gap `mean |p_a + p_a_swap - 1|` (0 = perfect) | 0.0688 | 0.0599 |

Read this way: the symmetric baseline is 50.0%. The model beats it by **+4.9pp** OOS — small edge, high variance at n=102.

## 3. Winner accuracy by weight class (true OOS)

Filter: `>= 5 fights` in the OOS window. `Fav-side acc` restricts to fights where model conviction >= 55%. Weight class is `Unknown` when the ESPN scraper didn't tag it.

| Weight class | n | Acc | n(conv>=55%) | Fav-side acc |
|---|---:|---:|---:|---:|
| Unknown | 28 | 57.1% | 25 | 60.0% |
| Middleweight | 5 | 40.0% | 5 | 40.0% |

## 4. Calibration curve (true OOS)

Bucket by model conviction `max(p, 1-p)`. If the model is calibrated, `avg_pred ≈ actual` in every bin. Bins with `actual << avg_pred` are **overconfident** — the leading indicator we saw for the `_project_ufc_model_broken_817` memo.

| Conviction bin | n | Avg predicted | Actual hit rate | Delta |
|---|---:|---:|---:|---:|
| 50–55% | 7 | 53.4% | 71.4% | +18.0pp |
| 55–60% | 6 | 56.4% | 33.3% | -23.1pp |
| 60–65% | 9 | 61.7% | 55.6% | -6.2pp |
| 65–70% | 9 | 66.8% | 44.4% | -22.3pp |
| 70–75% | 7 | 71.5% | 57.1% | -14.3pp |
| 75–80% | 6 | 77.5% | 50.0% | -27.5pp |
| 80–101% | 7 | 87.0% | 85.7% | -1.3pp |

## 5. Method model

- Requested holdout (leaky) argmax accuracy: **76.7%** (n=681)  
- True OOS argmax accuracy: **31.4%** (n=51)

**Per-class recall (true OOS)** — how often the model gets each actual method right:

| Actual | n | Recall (argmax correct) |
|---|---:|---:|
| KO/TKO | 18 | 5.6% |
| SUB | 15 | 0.0% |
| DEC | 18 | 83.3% |

**Confusion matrix (rows=actual, cols=predicted)**:

| | pred KO | pred SUB | pred DEC |
|---|---:|---:|---:|
| actual KO/TKO | 1 | 0 | 17 |
| actual SUB | 2 | 0 | 13 |
| actual DEC | 3 | 0 | 15 |

## 6. Distance model

- Requested holdout accuracy (thresh 0.5): **79.6%** (n=681)
- True OOS accuracy: **41.2%** (n=51)
- Base-rate baseline (always predict the majority class): leaky 50.2%, OOS 64.7%

## 7. Failure cohorts (accuracy below -110 break-even of 52.4%)

_Accuracy shown in natural DB orientation (a-biased) — reader should focus on relative gaps between slices, not absolute levels._

| Slice | n | Accuracy |
|---|---:|---:|
| wc:Middleweight | 5 | 40.0% |

## 8. Winner-model feature importance (top 15 by gain)

| # | Feature | Gain |
|---:|---|---:|
| 1 | `age_diff` | 14.50 |
| 2 | `a_str_acc` | 10.18 |
| 3 | `str_def_diff` | 8.81 |
| 4 | `b_str_acc` | 7.05 |
| 5 | `b_sapm` | 6.73 |
| 6 | `b_grappling_threat` | 6.54 |
| 7 | `a_sapm` | 6.32 |
| 8 | `a_grappling_threat` | 6.25 |
| 9 | `a_striking_dominance` | 6.16 |
| 10 | `sub_threat_diff` | 5.71 |
| 11 | `slpm_diff` | 5.47 |
| 12 | `b_losses` | 5.43 |
| 13 | `b_td_def` | 5.38 |
| 14 | `b_td_acc` | 5.30 |
| 15 | `b_total_fights` | 5.28 |

## 9. Verdict — launch-ready?

**Winner model: barely above coin-flip.** True out-of-sample symmetric accuracy is **54.9%** on n=102 scored orientations — +4.9pp above the 50% symmetric baseline. That's directionally correct but the edge is small and the sample is thin (51 unique fights). Compared to the training-time val_accuracy of 67.8%, real-world performance has regressed ~11 percentage points — the val split was leaky/optimistic as expected from the random 80/20 methodology.

**Overconfidence in the 55-80% conviction band.** The 75–80% bucket predicted 77.5% but hit only 50.0% (n=6). Multiple mid-band buckets show 14-27pp negative delta, which is exactly the failure mode `project_ufc_model_broken_817` flagged. Only the 80%+ extreme conviction bucket is calibrated. Any PRIME/STRONG tier gate that treats 60-75% raw probability as high-conviction WILL bleed units.

**Method model: broken.** OOS argmax accuracy 31.4%. The confusion matrix shows the model predicts DEC on almost everything — KO/TKO recall 5.6%, SUB recall 0.0%. It has effectively collapsed into a single-class predictor. **Do not surface method props (Fight Ends by KO/SUB/DEC) at launch.**

**Distance model: worse than majority-class baseline.** OOS accuracy 41.2% vs the base-rate baseline of 64.7% ("always predict finished"). Model is inverted or miscalibrated on this head. **Do not surface Fight Goes The Distance props at launch.**

**Recommendation: DO NOT ship UFC picks publicly at launch.** The natural-orientation conviction-weighted hit rate is 54.5% (n=44) and the fair symmetric accuracy is 54.9% — the winner model provides a real but marginal signal, the method model is collapsed, and the distance model is inverted. Ship options:

1. **Shadow mode only** (recommended for launch week 1-2): keep UFC in the pipeline for internal tracking, no public PRIMEs/STRONGs on the Sweat Locker card until we have >=100 OOS fights and >=55% symmetric accuracy.  
2. **Winner-only, gated hard** (if shipping is non-negotiable): hide method + distance heads entirely, cap surfaced picks to raw p >= 0.65 in one orientation AND raw p >= 0.65 in the swapped orientation for the same side (i.e. the model must agree with itself under symmetry), max 1 PRIME per event. This will drop volume ~80% but the retained picks should clear break-even.  
3. **Retrain v2 before launch** (the right answer): time-split the training set (train pre-2025-01-01, validate 2025+), retrain the calibrator on the actual OOS window, fix the method-model class imbalance with sample weights (SUB is clearly under-weighted), and re-run this backtest as the gate.
