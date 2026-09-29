---
name: project-total-model-retrain-625
description: 6/23 retrain initiative — composite went 3/12 yesterday, retrained model hit 7/11 (64%) — backfill plan to push to 75%+
metadata:
  type: project
---

6/23 audit revealed catastrophic composite miss on 6/22 (11/12 UNDERs, composite went ~3/12).

**Retrained ML on existing mlb_game_results data:**
- LogReg/RF/GB with 16 features: **7/11 (64%) on yesterday** vs composite's ~25%
- +39 percentage point improvement from retrain alone, no new data

**Top features by GradientBoost importance:**
1. sp_xera_max (worse SP) — 13%
2. k_pct_avg — 10%
3. sp_l3_min (best L3 ERA) — 10%
4. gap_v4 (v4 vs line) — 8%
5. bp_avg, park_run_factor — 7-8%
- **signal_confluence_net only 0.9% importance** (we've been overweighting noise)

**Conf-UNDER hits 70% (16/23) on LogReg holdout — UNDER signal exists, we under-publish UNDERs.**

**Blockers to 75%+:**
- pitcher_vs_team_era: 100% null in archive (need backfill)
- first_inning_era: 100% null (need backfill)
- home/away_ops_last7/14: 91% null (need backfill)
- jerry predictions: 90% null
- v4 predictions: 84% null
- model_pred_total only 575/3503 rows populated

**Why:** mlb_game_results pipeline writes raw scrape data but NOT all features used by current scorer.

**Backfill priority (saved memory):**
1. pitcher_vs_team_era from MLB API — high importance per `project_pitcher_vs_team_mastery_validation`
2. ops_last7/14 from existing daily snapshots
3. day-of-week / day-game-flag (Sunday-getaway pattern)
4. first_inning_era
5. SP form trend (xera_vs_l3_delta)

**Why:** 6/22 composite disaster (-4.2r avg overshoot across 12 games) revealed retrained model on existing features ALREADY beats composite by 39pt. Fix the data nulls and we can hit 75%+.

**How to apply:** When user pushes for model fixes, lead with backfill + retrain plan, not OVER-suppression patches. Suppression is a band-aid; retraining on richer features is the real fix.

Related: [[project_v4_over_drift]], [[project_v5_retrain_no_change]], [[project_pitcher_vs_team_mastery_validation]], [[feedback_no_hunches_only_backtest]]
