---
name: model-architecture-and-xgboost-role-clarified
description: "System is a hybrid — XGBoost is ONE narrow regression input; real edges come from audit-validated cohort rules, multi-signal confluence, mastery flags, and per-scorer logic. Post-launch options to make pattern mining more \"deep system\" feel."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

## What XGBoost is in this codebase

A **single multivariate regression model** that predicts two numbers (home_runs, away_runs) from 28 features. Walk-forward trained on resolved games. Used as one signal in the spread/total prediction.

**It is NOT:**
- A pattern-mining rule learner
- A pitcher-vs-batter matchup model
- A team-on-streak situational analyzer
- A multi-cohort specialist

## What does the actual work

The "system" isn't XGBoost — it's a **layered hybrid** the user might not realize they built:

### Layer 1 — Audit-validated cohort rules
Codified in `audit_tier_calibration.py` + prop scorers. These are *segment-specific* rules with empirical hit rates. The strongest cohorts are doing more work than any single model:

| Rule | STD hit % | n |
|---|---|---|
| `k_over_with_total_under` | 75.7% | 37 |
| `nrfi_prime_90_94` | 72.4% | 29 |
| `Outs UNDER STRONG` | 100% | 10 |
| `xera_gap_2_3_over` | 59.7% | 63 |

### Layer 2 — Multi-signal confluence
`game_context.py` builds `signal_confluence_breakdown` from 6-8 independent voters (xERA, L3 ERA, L3 K%, bullpen, recency, wrc_hand, park, mastery). The "PRIME +4" tier label is a rule, not a learned model.

### Layer 3 — Mastery / anti-mastery flags
`pitcher_vs_team_era` with min-15-IP and 1.5-ERA-delta gate. The closest thing to "team X vs pitcher Y" pattern matching. Hand-coded threshold, not learned.

### Layer 4 — Per-scorer logic
`generate_props.py` has 6 scorers (Ks, Outs, ER, BB, Hits, HA). Each has 5-10 signal checks with threshold-based bonuses. Calibrated against audit cohorts.

### XGBoost's actual role
One input feeding `model_pred_total` and `model_pred_spread` into context. Becomes a small voter (+6-10 pts) in the sweat score and one of several inputs to confluence. **Not load-bearing for card-grade plays.**

## Why XGBoost can't do deep pattern mining at our scale

- **619 games × 28 features** is small for the feature space (many correlated features)
- **High per-game variance** — MLB games have ~3.5 run MAE noise floor
- **No team / pitcher identity** in features — can't learn "Yankees offense vs LHP" patterns
- **No time-series / rolling features beyond L3 ERA** — can't learn season-state effects
- **Single global model** — can't specialize for NRFI vs spread vs total cohorts

## Post-launch options to actually do "deep pattern mining"

### A. Decision-tree rule learner (CART/RIPPER)
- Outputs interpretable rules like "if home_xera > 4 AND opp_wRC+ > 110 AND bullpen_relievers_3d ≥ 10 → OVER 60%"
- Publishable as social content ("the model found this rule")
- Same training data, different ML approach

### B. Per-cohort XGBoost models
- One model for NRFI sweet-spot games
- One for Coors-environment games
- One for K-prop x total UNDER setups
- Each specialized, smaller feature space, better signal-to-noise per cohort

### C. Team / pitcher embeddings
- Learn vector per team + per pitcher
- Matchup score = dot product of vectors
- Needs more data than n=619 — defer to year 2

### D. Sequential / season-state features
- Days since last home game (have it)
- Travel distance L3
- Bullpen-fatigue ratio (relievers used L3d / available)
- Schedule difficulty L7
- Better features into the existing model

## Recommendation order (post-launch)

1. **Option B (cohort-specific XGBoost)** — one model per playable angle. Highest-EV improvement.
2. **Option A (CART rule discovery)** — turns audit cohorts into publishable social content
3. **Recency-weighted retrain of existing global model** — quick win for `model_pred_total` accuracy
4. **Option D (better features)** — feed Option B with better signals

## Honest current-state for the user

"Our model isn't really XGBoost — it's a 4-layer rule + cohort + confluence + scorer system with XGBoost as one auxiliary input. The 72% NRFI 90-94 hit rate, 76% K-over-with-total-under, 100% Outs UNDER STRONG numbers are what proves the system works. XGBoost regression on 619 games is a marginal +/-5 point signal at best."

## Related
- [[project_may17_xgboost_degradation.md]] — current model degraded from 61.7% → 53.5% direction acc
- [[project_xgboost_spread_model_priority.md]] — original XGBoost decision
- [[project_v2_ensemble_models.md]] — original post-launch ensemble plan
- [[project_pitcher_class_projections.md]] — alternative projection path (per-starter)
