---
name: XGBoost runs model — top priority pre-launch (revised 2026-04-24)
description: Replace hand-coded spread/total formulas with one XGBoost regressor that predicts home_runs and away_runs separately. Derive spread (diff) + total (sum) from same model. Top priority 2026-04-25.
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
**Decision (revised 2026-04-24 evening):** Skip incremental formula upgrades (V4 attempt failed — adding splits + platoon to hand-coded formula did not beat baseline in backtest). Go straight to a learned XGBoost regressor that predicts `home_runs` and `away_runs` separately, then derive both spread (diff) and total (sum) from the same model.

**Architectural rationale:**
- Hand-coded formula's ceiling is bound by guessed coefficients (0.6 starter, 0.4 bullpen, 4.25 baselines)
- We pull ~50 features per game; projection uses only 5
- Predicting runs SEPARATELY (not just spread) gives both spread and total products from one trained model — doubles ROI per training cycle
- XGBoost handles missing values natively (critical — historical bullpen ERA only on 13% of rows), handles non-linear interactions for free (no manual feature engineering for park×wind, pitcher×platoon), and beats linear models on tabular data with ~25 features
- Confluence layer (shipped 2026-04-24, commit ac18926) becomes one feature input rather than the gate

**Tomorrow morning's sequencing (2026-04-25):**

**Phase 0 — Verify overnight (~30 min):**
- Check overnight pipeline ran clean with confluence layer
- Confirm signal_confluence_net populating, no upload failures
- Spot-check at least one game's confluence breakdown logged correctly

**Phase 1 — Backfill training data (~2-3hr):**
- Pull rows in `mlb_game_results` where critical columns are null (bullpen_era, home/away_pitcher_home_era, splits, framing, OAA, xwOBA)
- Backfill from MLB Stats API + Savant where data is reconstructable
- Goal: get historical training rows from ~13% bullpen coverage to 60%+
- Where backfill impossible, leave null (XGBoost handles)

**Phase 2 — Write training script (~4-6hr): `train_runs_model.py`**
- **Two targets:** `home_runs`, `away_runs` (train two separate XGBoost regressors)
- **Features (~25):**
  - Pitcher: xera, fip (if available), k_pct, bb_pct, whiff_rate, gb_pct, l3_era, l3_k_pct, days_rest, vs_team_era, home/away splits
  - Offense: wrc_plus (season), wrc_vs_opp_hand, lineup_weight, woba, xwoba, k_pct, barrel_pct
  - Bullpen: bullpen_era, bp_relievers_3d (workload)
  - Defense: team_oaa (apply to OPPOSING runs), catcher_framing
  - Environment: park_run_factor, temperature, wind_mph, wind_direction, dome_game
  - Officiating: umpire_k_rate, umpire_over_rate
  - Context: home_field (binary), days_since_last_home_game, consecutive_road_games, timezone_change, series_game_number, injury_count
  - Engineered: confluence_net (from current pipeline)
- **Validation:** WALK-FORWARD (train through day N, test day N+1, slide forward). Static train/test split is forbidden — mimics production.
- **Output:** `home_runs_model.pkl` + `away_runs_model.pkl`, both versioned with date stamp + feature schema JSON

**Phase 3 — Validation gate (~2-3hr):**
- Walk-forward on last 30 days
- Compare to current 5-input formula on:
  - MAE on home_runs and away_runs
  - Derived spread MAE (predicted_diff vs actual_diff)
  - Derived total MAE (predicted_sum vs actual_sum)
  - Spread direction hit rate
  - Total over/under hit rate
- **DO NOT SHIP IF:**
  - Spread direction hit rate < current 45%
  - Spread MAE > 1.5 runs
  - Total MAE > 2.0 runs
  - Held-out performance drops >10pts vs training (overfit)

**Phase 4 — Deploy (~2-3hr):**
- Replace the entire spread+total projection blocks in `game_context.py` (lines ~2080-2220) with model.predict() calls
- Load model files at module init
- Same downstream confluence flow continues to work (confluence_net becomes a feature, not a gate)
- Keep V3 hand-coded formula as fallback if model file missing

**Phase 5 — CLV tracking (separate ~1-2hr):**
- Add closing_line_value column to mlb_game_results
- After each game, compute: did our pick close at a better line than what we recorded? CLV is the long-term profitability metric, not hit rate.
- Surface in audit dashboard (docket #9)

**Phase 6 — Monthly retrain cron (after v1 ships):**
- GitHub Actions cron, 1st of each month
- Pull last 90 days of resolved games
- Retrain both models, validate against current production model
- Auto-deploy if new model beats current by ≥1pt MAE on held-out set

**Realistic time:** 1-2 focused days for Phases 1-4 if backfill is straightforward. Phase 5+6 can come in week 2.

**Key insights from external review (2026-04-24):**
- Predicting `home_runs` and `away_runs` SEPARATELY (not just spread) is the architectural unlock
- Apply defense (OAA) ASYMMETRICALLY — reduce opponent's expected runs, not generic factor
- Composite pitcher quality (xera + fip + K-BB) is better than raw xera — but doesn't matter for XGBoost since it learns interactions
- Walk-forward validation is non-negotiable
- CLV > hit rate as the real money metric
- Bayesian shrinkage on small-sample stats — XGBoost handles via tree depth + min_samples_leaf, less critical than for linear models

**Why:** User explicitly green-lit the proper rebuild (2026-04-24 evening). Hand-tuned formula + bolted-on confluence is acceptable patch for short-term but not the launch architecture. The data plumbing is the moat — most retail bettors have a model and no data; SweatShop has data and a naive model. Flipping that ratio is the launch-blocking workstream.

**How to apply:** This is the next major workstream after tonight's confluence layer is verified stable. All other model improvements (pitcher splits, weather for spread, recent form) become FEATURES in the trained model rather than standalone formula tweaks. Reference docket item #15 for monthly retrain cadence pattern. Same architecture extends to NBA/NFL/NCAAF when those launch — train one runs/points model per sport, derive spread/total from it.
