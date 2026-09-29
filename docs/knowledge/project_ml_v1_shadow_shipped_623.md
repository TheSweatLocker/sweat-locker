---
name: project-ml-v1-shadow-shipped-623
description: 6/23 shipped ML v1 XGBoost in shadow mode (53% holdout / 64% conf-HOME on n=42); 14d gate before production swap
metadata:
  type: project
---

User accepted ML retrain after totals shadow shipped (6/23 same session).

**Shipped:**
- `models/ml_v1_xgb.pkl`: XGBoost on Set B_spread_fusion (17 features, 208 clean rows)
- `shadow_ml_inference.py`: scores each game with v1, writes jerry_cache[ml_v1_shadow_<date>]
- `audit_ml_v1_shadow.py`: v1 vs composite vs actuals
- Added to `.github/workflows/mlb_pipeline.yml` after v7 totals shadow

**Production rules:**
- p_home >= 0.60 = HOME conf (64% hist) → eligible to publish IF validates
- p_home <= 0.40 = AWAY conf (44% hist, no edge) → log only
- 0.40 < p < 0.60 = lean → log only

**Promotion gate:** 14 days of shadow; if v1 net wins vs composite, swap to production.

**Top features (XGBoost Set B):**
1. spread_avg (composite v3+v4+jerry): 15%
2. sp_l3_diff (recent SP form delta): 15%
3. sp_k_pct_diff: 12%
4. bp_diff: 11%
5. day_of_week: 9%
6. market_p_home (closing odds implied): 8%

**Caveat noted day-1:** Model called HOME on 10 of 15 games tonight, including big upsets vs market (BOS@COL HOME 85% at +136, ATL@SD HOME 96%). Could be real edge OR overfit on home-bias. 14d shadow will tell.

**Why:** Mirror of v7 totals retrain methodology. Same discovery → train → shadow → 14d gate.

**How to apply:** Do not publish HOME ML picks from v1 until 14d audit clears. Composite stays the user-facing scorer.

Related: [[project_total_v7_shadow_shipped_623]], [[project_total_model_retrain_625]]
