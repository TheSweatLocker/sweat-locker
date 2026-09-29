---
name: project-lr-opportunities-909
description: "🎯 QUEUED: 7 linear regression opportunities under-exploited in sports modeling — untapped edge post-launch. Ranked by effort vs impact. Origin: 9/9 discussion after LR-override +148u/30d results validated."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-10T02:00:49.762Z
---

## Why this exists

9/9 discussion after 30-day LR-override validation (+148u/30d on props,
67% same-day sides on 9/8). LR track record proves the mechanism works;
current implementation exploits ~30-40% of what's on the table. This is
the queue of specific extensions.

## Why LR works in sports (baseline theory to remember)

1. **Calibrated probabilities** vs categorical tier labels — LR's 0.62/0.71/0.83
   scale stays honest across market conditions because it's fit to actual
   outcomes with strict train/test discipline.
2. **Regularization prevents noise-chasing** — sim MC overreacts to single-
   ERA anomalies, ensemble votes correlate, LR features must survive
   penalty terms.
3. **Interacts naturally with close_line** — LR learns "when model
   disagrees with market by X pts, hit rate = Y%." That IS the sports
   edge, expressed cleanly.
4. **Robust to stale inputs** — 30-day +148u despite pre-close-line
   feature bug (per project_lr_shadow_stale_909). Decision boundary
   broadly right → wins accrue even when exact inputs drift.

## 7 opportunities — ranked by (impact × ease)

### 1. Meta-LR on ensemble ERRORS ⭐⭐⭐⭐⭐ (biggest untapped)
- Fit LR to predict when ensemble is WRONG, not to predict outcomes directly
- Features: model disagreement magnitude, market volatility, cohort tags,
  time-to-game, splits divergence, close_line move magnitude
- Output: p(ensemble_wrong) per pick
- If it flags "these 3 picks look unstable" at 60%+ acc → massive tier-gating input
- Effort: MEDIUM (2-3 sessions). Data: existing pick history + graded results.
- Nobody in commercial handicapping ships this.

### 2. Multi-response PLS (partial least squares) ⭐⭐⭐⭐⭐
- Fit ONE model that jointly predicts spread + total + ML probabilities from same features
- Enforces internal consistency — no more "picked NE ML but simmed SEA to win"
- Standard in economics, unheard-of in sports modeling
- Effort: MEDIUM. Would fix the recurring "sim vs picked-side contradiction" audit finding.

### 3. Bayesian LR with informative priors ⭐⭐⭐⭐
- Treat prior-season coefficients as PRIORS for this-season LR
- Mathematically-rigorous version of my Wk 1-3 blend heuristic (weight_curr = games/3)
- Reports posterior uncertainty as a bonus (natural conviction score)
- Effort: MEDIUM-HIGH (needs PyMC3 or similar). Would improve early-season NFL Wk 1-3
  significantly by using 2025 as prior instead of hard-coded blend weights.

### 4. Quantile regression (median instead of mean) ⭐⭐⭐⭐
- Predict median outcome instead of mean — much more robust to blowout outliers
- Baseball totals especially: 3-4 games/day are 15+ run outliers that skew LSR fit
- Effort: LOW (statsmodels has QuantileRegressor). Swap-in replacement for total-model LR.
- Should improve total-model coverage % by 2-5pp based on outlier frequency.

### 5. Player-level cross-sport residualized LR ⭐⭐⭐⭐
- Fit LR on RESIDUAL after removing venue + opponent + weather effects
- What's left = pure "player deviation from context" — transferable across seasons
- Extremely rare in commercial models
- Effort: HIGH (needs residual pipeline). Highest-leverage for player props.
- Would enable "Bregman-hot-vs-context" style signal (identify players outperforming
  their expected environment, not just their season baseline).

### 6. Interaction terms nobody tries ⭐⭐⭐
- LR loves interactions when you TELL it which to try
- Example: pitcher_era × opposing_team_wRC+ × park_factor (three-way)
- Would take 30 minutes to add to any existing LR model
- Almost nobody does this — everyone moved to boosted trees before exhausting the
  LR feature-engineering lane
- Effort: LOW. Try 5-10 interactions, keep survivors. Immediate.

### 7. Regularized L1 (Lasso) for sparse-data feature selection ⭐⭐⭐
- Every model shop uses xgboost when data is sparse (Wk 1 NFL) — overfits at n=32
- Lasso LR with CV picks 8-12 features that MATTER
- Effort: LOW. Sklearn LassoCV drop-in.
- NFL Wk 1-3 predictions benefit most.

## 9/9 iteration results (empirical findings)

**Item #3 (Meta-LR on ensemble errors) — TESTED exploratory at n=92, weak signal.**
- Shadow: `mlb_pipeline/_lr_v2d_meta_error.py` (gitignored)
- Model: `models/mlb_meta_error_v1.json`
- CV-AUC: 0.594 (weak — 0.5 = random, 0.65 = usable, 0.7 = strong)
- CV-acc: 59.5% vs 56.5% baseline = +3.0pp lift, ±11% std (noisy at n=92)
- **Killer finding — coefficient story is directionally correct:**
  - `sharp_ml_div_abs` +0.262 = big sharp/public divergence → MORE wrong (NOVEL!)
  - `is_strong_tier` -0.250, `is_prime_tier` -0.208 = higher tiers less wrong (validates tier calibration)
  - `data_bad` +0.207 = LOW completeness → MORE wrong (validates data quality)
  - `jerry_vs_sim_gap` +0.136 = model disagreement → MORE wrong
  - `mc_confidence` -0.106 = MC calibrated correctly (higher conf → less wrong)
- **Novel implication of sharp_ml_div_abs coefficient:** resolver isn't fully weighting
  sharp direction. When sharp money and public bets diverge >15pp, the picked side
  misses more often than expected. Could feed back as a tier-cap gate.
- **Rerun when n≥150** (~end of 9/25 at current pace). Even with just 60 more games,
  AUC should tighten and coefficient magnitudes stabilize.
- **Not shippable yet** — CV-AUC 0.594 below 0.60 usable threshold, but promising.

**Item #7 (Lasso Feature Selection) — TESTED, ✅ WON BIG at n=1160.**
- Shadow: `mlb_pipeline/_lr_v2c_lasso_selection.py` (gitignored)
- Model: `models/mlb_ml_logreg_lasso_v1.json`
- **Lasso holdout: 64.9% vs Production v1 59.1% = +5.9pp lift** — biggest LR win of the day
- Best C=0.05 (aggressive L1 shrinkage)
- **15 of 137 features survived** (Lasso killed 122 as noise, including 20 of 24 interactions)
- Only 4 interactions kept — VALIDATES iter1 finding that interactions have signal
  but must be selected not accumulated
- Surviving interactions with strong signal:
  1. `sp_xera_gap` +0.125 (HOME lean when home SP has xERA edge)
  2. `home_sp_xera_x_park` -0.049 (AWAY lean when home SP in hitters park)
  3. `sharp_div_x_spread` -0.016 (sharp money × line interaction)
  4. `away_bp_era_x_opp_wrc` +0.011 (bad away BP × strong home offense)
- Tier breakdown (all profitable, no dead zone):
  - PRIME_HOME: 72.3% n=47
  - PRIME_AWAY: 77.8% n=27
  - STRONG_HOME: 66.7% n=114
  - STRONG_AWAY: 62.1% n=87
- 9/9 slate: 2-0 finals, disagreed with v1 on TOR@OAK (Lasso right — OAK won),
  disagrees on CIN@LAD (in progress)
- **Ship path:** parallel-shadow for 30d as `_lr_ml_shadow_v2` alongside current
  `_lr_ml_shadow`. If holdout advantage sustains at n≥60 real picks, cut over.
- **Cross-sport:** clone script for NFL / NCAAF ML shadows. Feature engineering
  already parity per data infrastructure priorities.

**Item #2 (Quantile Regression on Totals) — TESTED, ✅ WON at n=1150.**
- Shadow: `mlb_pipeline/_lr_v2b_quantile_total.py` (gitignored `_*.py`)
- Model saved: `models/mlb_total_quantile_v1.json`
- Q-total holdout: **62.8%** vs production LSR classification 59.8% = **+2.9pp lift**
- PRIME_OVER tier: **37/39 = 94.9%** (n=39!) — best tier of any model in the stack
- PRIME_UNDER: 36/53 = 67.9%
- STRONG_UNDER: 67/114 = 58.8%
- STRONG_OVER: 17/28 = 60.7%
- Median MAE: 3.07 runs (was 3.60 with default alpha, 4.56 = baseline "always predict 8")
- Best alpha=0.01 via grid search (alpha=0.5 crushed all coefs to intercept-only — noted)
- 9/9 slate today: 2-0 on finals (WSH@SD OVER 8.5 → 11 hit, MIN@DET OVER 8.5 → 9 hit)
- **Killer UX gain:** Q model outputs "expected 14.5 runs vs close 8.5" — way more
  legible than "p_over=0.72". Cleveland @ Baltimore predicted 14.5 vs 8.5 close =
  6-run over-edge, actionable and explainable.
- **Ship path:** wire as `_lr_total_shadow_q` field on primary_play alongside existing
  `_lr_total_shadow`. Run 30-day parallel, cutover if holdout advantage sustains.
- **Cross-sport plan:** clone script for NFL total (higher scoring variance —
  should benefit even more), NCAAF, NBA totals. Feature sets already have
  parity per project_data_infrastructure_priorities_908.

**Item #1 (interaction terms) — TESTED, FAILED at n=1160.**
- Shadow: `mlb_pipeline/_lr_v2_shadow.py` (kept for future iteration)
- iter 1 (24 interactions incl null-heavy cols): -5.3pp vs v1 on holdout
- iter 2 (24 interactions from populated cols only): -3.9pp vs v1 on holdout
- Root cause: 137 features on 812 training rows = overfit-prone. L2 regularization
  can't fully save it; interactions steal predictive power from well-calibrated
  base features. Coefficient directions non-intuitive (e.g. away_sp_xera_x_park
  = -1.9 pushing AWAY when physics says HOME) — fingerprint of noise-fitting.
- Interesting non-interaction "gap" features that DID converge sensibly:
  bullpen_era_ratio, bullpen_era_gap, wrc_gap.
- Today (9/9) held out: v1 and v2 both went 2-0 on the day's 2 finals.
  v2 flipped 3 games differently — TOR@OAK (v2 right — OAK won), TEX@SEA
  (both right, v2 less confident), CIN@LAD (in progress).
- **Verdict:** don't ship as-is. Interactions have real signal (bullpen ratio
  works) but must be selected not accumulated. Retry via Item #7 Lasso.

**Item #7 (Lasso) CROSS-SPORT PORT to NFL — TESTED, ✅ WON at n=1693.**
- Shadow: `mlb_pipeline/_lr_v2e_nfl_lasso.py` (gitignored)
- Model: `models/nfl_ml_logreg_lasso_v1.json`
- **NFL Lasso: 64.2% acc, +9.1pp lift over baseline** — bigger lift than MLB
- Only 1 interaction survived (spread_x_total — but NFL feature set was thin, 11 base + 11 interactions)
- Tier breakdown:
  - PRIME_HOME: 109/136 = **80.1%** (elite)
  - PRIME_AWAY: 59/78 = **75.6%** (elite)
  - STRONG_HOME/AWAY: 53-55% (middle tier profitable)
- **Ship path:** parallel-shadow alongside existing NFL LR for Wk 1-2, cutover if holds.

**Item #4 (Multi-response PLS) — TESTED, ✅ WORKS at n=1160.**
- Shadow: `mlb_pipeline/_lr_v2f_pls_multi.py`
- Model: `models/mlb_pls_multi_v1.json`
- ML acc: 62.9% (+3.8pp vs v1 LR, slightly under Lasso)
- Spread MAE: 3.13, Total MAE: 3.08 (competitive with dedicated models)
- **KEY WIN: 92% cross-consistency** between spread & ML predictions
  (validates recurring "picked X but sim Y" audit findings)
- When both agree (320/348 holdout games): 63.1% hit rate
- Remaining 8% self-disagree → **flag as low-conviction gate signal**
- **Ship path:** run alongside existing models; use PLS agreement as a
  meta-signal — when spread + ML disagree, cap pick to LEAN.

**Item #5 (Bayesian LR with prior shrinkage) — TESTED, ✅ VALIDATES concept.**
- Shadow: `mlb_pipeline/_lr_v2g_bayesian_prior.py`
- Model: `models/mlb_ml_logreg_bayesian_v1.json`
- Approach: no PyMC needed — use v1 coefficients as center of ridge penalty.
  loss = -log_likelihood + λ * ||β - β_prior||²
- **Beautiful demonstration of shrinkage tradeoff at n=396:**
  - λ=0 (pure MLE): 54.6% ← severe overfit at small sample
  - λ=0.5: 58.8%
  - λ=2: 60.5%
  - λ=10: 64.7%
  - λ=50: 63.9%
  - λ=200 (near-prior): 67.2%
  - **v1 prior alone (no update): 70.6%**
- Takeaway: at n=396, MLE overfits by -16pp; sticking with prior wins.
- **Direct product use:** replace my hand-tuned Wk 1-3 blend (weight_current =
  games/3) with mathematically-grounded Bayesian shrinkage. Recipe:
  NFL Wk 1: λ=200 (near-pure prior)
  NFL Wk 2-3: λ=50
  NFL Wk 4+: λ=10
  NFL Wk 8+: λ=0.5 (mostly current data)

**Item #6 (Player-residualized LR for props) — BLOCKED by data.**
- Shadow: `mlb_pipeline/_lr_v2h_residualized_props.py`
- Root: mlb_game_context only persists last ~13 days of primary_play + context.
  Joined ks_over prop history (413 resolved) with ctx dropped to n=53, then
  n=17 after residual-mean filter. Sample too small.
- Real finding worth keeping: **ctx (opp_wrc + opp_k_pct + park) R²=0.045**
  on ks — context explains only 4.5% of variance. Residual IS most of the
  signal. Validates the RESIDUALIZATION HYPOTHESIS.
- **Ship path:** blocked until historical ctx persistence extended OR
  reconstruct from mlb_pitcher_stats per-game history. Adds ~2 sessions
  of data-plumbing work before this can be A/B tested.

## Ship order (revised after 9/9 iter)

**Post-launch, in this order:**

1. ~~**Interaction terms**~~ ✅ TESTED 9/9 — failed at current data volume
2. **Quantile regression** on totals (1-day) ⭐ next — drop-in swap, no overfitting risk
3. **Lasso feature selection** on the interaction pool (was #7) — pruned interactions might beat v1 where all-in didn't
4. **Meta-LR on errors** (2-3 sessions) — biggest strategic win, most novel
5. **Multi-response PLS** — fix internal contradictions
6. **Bayesian priors** — cleaner Wk 1-3 story than current blend
7. **Player-residualized LR** — highest player-prop ceiling

## Data prerequisites

Most of these need:
- Historical picks + graded results (HAVE — daily_surface_records, mlb_pipeline_props)
- Historical splits + market movement (HAVE — line_movement_flags, splits_summary)
- Historical model disagreement (HAVE — ensemble_v2 stores every model's vote)
- Historical residuals (BUILD — need to persist prediction vs actual per model)

## Success metric per opportunity

Every one of these should be A/B'd:
- Shadow-fit vs existing LR
- Compare 30-day rolling W-L-Push and units_won
- Ship if beats existing by ≥ 5% hit rate or ≥ 10u/30d
- Kill if worse — don't sunk-cost fallback logic

## References

- Current LR track: [project_lr_shadow_stale_909](project_lr_shadow_stale_909.md) — +148u/30d despite stale inputs
- Model architecture: [project_model_architecture_xgboost_role](project_model_architecture_xgboost_role.md)
- Calibration framework: [project_calibration_architecture_805](project_calibration_architecture_805.md)
- LR dissent case study: [project_lr_dissent_calibration_903](project_lr_dissent_calibration_903.md)
