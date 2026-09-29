---
name: may-17-xgboost-runs-model-degradation
description: "XGBoost runs model direction accuracy dropped 61.7% (4/25, n=328) → 53.5% (5/17, n=619). Gates fail on retrain; deployed model stays. Post-launch: feature drift investigation or recency-weighted training."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

## What happened

Walk-forward validation of `train_runs_model.py` with current dataset (n=619 games, vs original n=328 from 4/25):

| Metric | 4/25 (training time) | 5/17 (current val) | Δ |
|---|---|---|---|
| direction_acc_xgb | **61.7%** | **53.5%** | **-8.2 pts** |
| direction_acc_formula | 56.4% | 52.7% | -3.7 pts |
| xgb lift vs formula | +5.3 pts ✓ | +0.8 pts ✗ | fails 3-pt gate |
| spread MAE (XGB) | 3.25 | 3.54 | +0.29 |
| total MAE (XGB) | 3.62 | 3.68 | +0.06 |

Validation gate: requires ≥3pt direction lift over formula. **Failed → no model save.** Production model remains the 4/25 version.

## Likely causes

1. **April overfit:** Original n=328 training was April-only (cold-weather games, slow early-season offenses). May data has different run environments — bullpens worked more, offenses settled, lineups confirmed.
2. **Feature drift:** Some features that correlated with outcomes in April may not in May (`wrc_hand_diff`, `nrfi_score` as a runs predictor proxy, etc.)
3. **Sample size doubled, noise doubled:** Direction accuracy at small n is volatile. 61.7% on n=328 had wider confidence intervals than the difference suggests.

Formula direction accuracy is **52.7% — barely above coin flip.** Our spread-direction edge is small to begin with; XGBoost was only marginally better.

## Why this isn't a launch blocker

Our actual edges aren't in the runs model:
- **POTD: NRFI sweet spot 90-94 cohort (72.4%)**
- **DOD: confluence direction-aligned plus money**
- **PRIME props (Outs UNDER STRONG 100%, etc.)**
- **Hits-under stacks (STRONG tier 64.6%)**

The runs model is auxiliary — it feeds `model_pred_total` and `model_pred_spread` for one input among many. The sweat-score rewrite uses it but heavily augmented with confluence + 1st-inn + mastery + prop signals.

## Post-launch options

### Option A — Feature engineering pass
Drop/replace features that became less predictive:
- Maybe `wrc_hand_diff` (lineup unconfirmed → uses season vs RHP/LHP, which is stale)
- Replace with rolling 14-day team R/G against opposing hand
- Add `bullpen_fatigue_ratio` (relievers used L3d / available)
- Add `confirmed_lineup_wrc_plus` when available

### Option B — Recency-weighted training
- Sample weight = exp(-(today - game_date) / 30)
- Last 30 days carry 1.0 weight, 60 days back ~0.4, 90+ days ~0.1
- Captures current conditions without throwing away historical data

### Option C — Ensemble + conservatism
- Keep 4/25 model + train recency model + average them
- Or use whichever predicts a smaller spread (avoid overconfidence)

### Option D — Accept the model is what it is
- Acknowledge runs model is only marginal edge
- Lean harder on cohort-based plays (NRFI, confluence, props)
- Don't ship a worse model just because data accumulated

## Recommendation

**Defer to post-launch.** No retrain until after Sunday 5/17-18 submission. Then run a 2-week experiment with Option B (recency weighting) and see if direction accuracy recovers.

## Production status

- **Deployed model:** 4/25 XGBoost (current)
- **Live data status:** Model used in cron pipelines, feeds sweat score + Daily Degen + game reads
- **Trust level:** Treat `model_pred_total` and `model_pred_spread` as informational, not as primary signals. Use confluence + props as anchors.

## Related
- [[project_xgboost_spread_model_priority]] — original 4/25 ship decision
- [[project_v2_ensemble_models]] — post-launch v2 plan (5-model ensemble)
- [[project_may15_calibration_notes]] — 5/17 audit docket
- [[project_may17_confluence_audit]] — same day's PRIME confluence degradation
