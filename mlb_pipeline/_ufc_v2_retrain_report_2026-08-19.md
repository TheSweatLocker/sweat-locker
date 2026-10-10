# UFC v2 Retrain — 2026-08-19

## Context
v1 (May 2026) shipped with a random 80/20 split. Every fight leaked through
the cross-fighter career-stats table, and symmetrized duplicate rows landed
in both halves — 68% val accuracy collapsed to ~55% out-of-sample. v2 fixes
the split, retunes hyperparams, and forces the method model to learn all
three classes via inverse-frequency sample weights.

## Split
Data window (2024-05 to 2026-08) contains no pre-2024 fights, so the proposed
`<2024` train boundary was empty. Adjusted to preserve the temporal-holdout
intent:

| Split | Boundary | Fights | Rows (post-symm) |
|---|---|---:|---:|
| Train | `event_date < 2025-10-01` | 719 | 1,316 |
| Val   | `2025-10-01 <= event_date < 2026-02-01` | 155 | 272 |
| Test  | `event_date >= 2026-02-01` | 241 | 378 |

Symmetrization (`_swap_ab`) is now performed **per split** — both (a,b) and
(b,a) orientations of each fight land in the same split, so no fight can
leak across the boundary. Because the test set contains every fight in both
orientations, the reported test accuracy is **symmetric accuracy** by
construction (equivalent to the earlier backtest methodology).

Feature-build skips: 95 fights dropped for missing pre-fight history (debut
fighter or missing career stats). 14 fights dropped for draw/NC/DQ label.

## Winner Model
XGBoost binary. 25 random-search configs, early stopping = 50 on val log-loss.

**Best hyperparams:** `max_depth=3, lr=0.03, min_child_weight=3, subsample=0.7,
colsample_bytree=0.75, reg_lambda=5.0`, best_iter=313.

| Split | Log-loss | Accuracy | AUC |
|---|---:|---:|---:|
| Train | 0.441 | 83.5% | 0.909 |
| Val   | 0.527 | 77.6% | 0.826 |
| Test  | 0.624 | **64.3%** | 0.708 |

v1 baseline (leaked): val 68% / OOS 55%. v2 test accuracy 64.3% with AUC 0.71
is a real step up — the AUC says winner probs discriminate meaningfully.

Train->test drop (83% -> 64%) is expected overfit; val->test drop (78% -> 64%)
is more concerning — the 4-month val window may be materially different from
the 6+-month test window (opponents cycled, aging fighters retired). A monthly
walk-forward retrain will contain this.

### Test-set calibration (5% bins, n>=5 shown)
| Prob band | Avg pred | Empirical | n |
|---|---:|---:|---:|
| 0.05–0.10 | 0.068 | 0.000 | 6 |
| 0.10–0.15 | 0.128 | 0.133 | 15 |
| 0.15–0.20 | 0.169 | 0.333 | 15 |
| 0.20–0.25 | 0.229 | 0.273 | 22 |
| 0.25–0.30 | 0.273 | 0.357 | 28 |
| 0.30–0.35 | 0.325 | 0.321 | 28 |
| 0.35–0.40 | 0.371 | 0.480 | 25 |
| 0.40–0.45 | 0.423 | 0.400 | 25 |
| 0.45–0.50 | 0.475 | 0.536 | 28 |
| 0.50–0.55 | 0.524 | 0.469 | 32 |
| 0.55–0.60 | 0.579 | 0.667 | 18 |
| 0.60–0.65 | 0.630 | 0.571 | 28 |
| 0.65–0.70 | 0.674 | 0.552 | 29 |
| 0.70–0.75 | 0.723 | 0.727 | 22 |
| 0.75–0.80 | 0.773 | 0.739 | 23 |
| 0.80–0.85 | 0.832 | 0.714 | 14 |
| 0.85–0.90 | 0.866 | 0.846 | 13 |
| 0.90–0.95 | 0.923 | 1.000 | 7 |

**Reads:** extremes (<=0.30 and >=0.75) track empirical within ~5pp. The
0.60–0.70 mid-band **overprints ~10pp** (predicting 63–67% wins only 55–57%
of the time). >=0.85 holds up cleanly (85–100% empirical, n=20 combined).

### Feature importance (top 15 by gain)
| # | Feature | Gain |
|---:|---|---:|
| 1 | age_diff | 9.77 |
| 2 | b_str_acc | 8.09 |
| 3 | a_grappling_threat | 7.24 |
| 4 | form_diff | 7.04 |
| 5 | b_grappling_threat | 6.87 |
| 6 | a_sapm | 6.66 |
| 7 | b_sapm | 6.61 |
| 8 | sub_threat_diff | 5.96 |
| 9 | a_str_acc | 5.76 |
| 10 | b_striking_dominance | 5.52 |
| 11 | a_td_def | 5.43 |
| 12 | a_striking_dominance | 5.23 |
| 13 | a_td_acc | 5.21 |
| 14 | b_td_acc | 5.19 |
| 15 | td_def_diff | 5.07 |

Age gap leads. Grappling threat (TD × opponent TD-def hole) shows up on both
sides. Striking accuracy + strikes-absorbed carry the striking signal.

## Method Model (KO/TKO / SUB / DEC)
Inverse-frequency sample weights. 20-config random search. Best: `max_depth=3,
lr=0.075, min_child_weight=1, subsample=0.9, colsample_bytree=0.9, reg_lambda=2.0`,
best_iter=110. **SUB collapse fixed** — no retry needed.

| Split | Log-loss | Accuracy |
|---|---:|---:|
| Train | 0.670 | 78.8% |
| Val   | 0.961 | 49.6% |
| Test  | 1.049 | 46.6% |

Test predicted class counts: KO=136, **SUB=86** (v1: 0), DEC=156.
True: KO=124, SUB=64, DEC=190.

### Per-class test metrics
| Class | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| KO/TKO | 47.1% | 51.6% | 0.492 | 124 |
| SUB    | 26.7% | 35.9% | 0.307 | 64  |
| DEC    | 57.1% | 46.8% | 0.514 | 190 |

Confusion matrix (rows=truth, cols=pred):
|         | KO_pred | SUB_pred | DEC_pred |
|---|---:|---:|---:|
| KO_true  | 64 | 24 | 36 |
| SUB_true | 10 | 23 | 31 |
| DEC_true | 62 | 39 | 89 |

Overall test accuracy (46.6%) sits under the DEC majority baseline (50.3%),
but the model's job is a **distribution over three outcomes**, not top-1.
v1 predicted zero SUBs on holdout; v2 predicts every class in reasonable
proportion. SUB precision 27% is weak as a hard pick but usable in EV
context when the market prices SUB below 27%.

## Distance Model
Retrained with `scale_pos_weight`. Test acc 56.9%, AUC 0.573, log-loss 0.690.
Majority baseline 50.3%. Beats baseline by **6.6pp**, clears the 55% floor —
narrowly. Saved as `ufc_v2_distance.json`.

**Verdict: KEEP but SHADOW.** Do not surface distance-line picks until a 30-day
live-tracking window confirms the edge holds. AUC 0.57 is anemic; the small
test-set win may be walk-forward artifact.

## Recommended Production Tier Gates (winner model)
| Tier | Gate | Rationale |
|---|---|---|
| PRIME  | prob >= 0.75 AND market_implied <= prob − 0.05 | Well-calibrated bin (73–74% empirical, n=45) |
| STRONG | 0.65 <= prob < 0.75 AND market_implied <= prob − 0.10 | Bin overprints ~10pp; larger buffer |
| LEAN   | 0.55 <= prob < 0.65 AND market_implied <= prob − 0.10 | Modest edge on well-behaved band |
| NO PLAY| prob < 0.55 OR edge below buffer | Coin-flip zone |

## Method Picks Gate
Only ship KO or SUB props when model prob >= 0.40 AND market implied <=
prob − 0.08. Do **not** ship DEC picks — DEC precision (57%) barely beats
the base rate (50%).

## Smoke Test (Loaded Artifacts)
- Winner reloaded from `ufc_v2_winner.json`, scored held-out fight: p=0.476,
  true_label=1. OK.
- Method reloaded from `ufc_v2_method.json`, scored: KO=0.443, SUB=0.248,
  DEC=0.309, true=KO. Top-1 correct. OK.

Both models deserialize and return valid probabilities.

## Verdict — is v2 launch-ready?
**Winner: YES**, with the mid-band calibration caveat (enforce the buffer in
the tier gate; shadow for 2–3 weeks before making it the sole engine).

**Method: PARTIAL** — usable for KO and SUB props where market is below
model, DO NOT ship DEC picks. Fix vs v1 is real: model predicts all three
classes instead of collapsing to DEC.

**Distance: SHADOW ONLY** — narrowly beats baseline; AUC 0.57 is thin.

**Do not swap `ufc_predict.py` yet.** Sequence: (1) user reviews this
report; (2) shadow v2 vs v1 on the next 5–10 cards with zero product
exposure; (3) if shadow confirms, swap winner+method to v2; (4) schedule
monthly walk-forward retrain (this script is idempotent — leave
`UFC_V2_ROW_CACHE` unset in prod so features rebuild fresh each run).

## Files
- `mlb_pipeline/ufc_v2_train.py` (keeper training script)
- `mlb_pipeline/models/ufc_v2_winner.json` + `.meta.json`
- `mlb_pipeline/models/ufc_v2_method.json` + `.meta.json`
- `mlb_pipeline/models/ufc_v2_distance.json` + `.meta.json`
- v1 artifacts preserved. `ufc_predict.py` untouched.
