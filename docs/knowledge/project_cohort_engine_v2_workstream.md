---
name: project-cohort-engine-v2-workstream
description: "Three-item cohort engine expansion workstream queued 2026-06-09 — model vote re-weighting (tonight), ML/RL cohort expansion, props cohort unification"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Three-item workstream defined 2026-06-09 evening, in priority order:

**1. Re-weight model votes by historical baseline (TONIGHT)**
   - File: `compute_primary_play` / model-blend logic
   - Current: v3, v4, Jerry, conf get equal vote weight in directional calls
   - Target: weight by historical accuracy from `play_baselines` (v3_tot 66.8% / v4_tot 56.0% / jerry_tot 50.0%); v3_ml 55.7% / v4_ml 53.2% / jerry_ml 53.4% / conf_ml 50.0%
   - Smallest contained change with the biggest leverage — the data already encodes which model to trust
   - Expected outcome: Jerry's noisy total predictions get demoted; v3 totals carry more weight; v4 mid-range; conf-only signals (~50%) get discounted

**2. ML/RL cohort expansion (1-week effort)**
   - Re-backtest with `min_n=7` instead of 10 to surface more ML/RL signals (totals dominate today's 667 rules because 2-outcome cleaner than 3-outcome RL)
   - Add ML-specific features to backtest: starter rest, bullpen 3d usage, lineup wOBA vs hand, pitcher-class, sharp-money flags, umpire tendencies
   - Goal: tonight's slate had 10-23 cohorts firing per game on totals but mostly 0 on ML/RL — that's the gap

**3. Props cohort unification (2-3 week post-launch)**
   - Apply 4-pass cohort backtest framework to props (hits/K/ER/outs scorers)
   - Currently prop tiers (PRIME/STRONG/LEAN) are hardcoded signal weights, not cohort-attributed
   - Needs 200+ graded props per type before stats stabilize — wait until after launch when sample grows

**Why:** 2026-06-09 conversation surfaced two findings that drove this workstream:
1. Tonight's slate showed total cohorts vastly outnumber ML/RL cohorts (10-23 vs 0-3 per game)
2. `play_baselines` in cohort_signals data reveal Jerry totals are 50% (random); v3 totals 66.8% (real edge) — yet model votes are equal-weighted today

**How to apply:**
- Item #1 is tonight's session — keep it contained, no scope creep into items #2 or #3
- Item #2 is feature-design work; needs slate-day-friendly feature set
- Item #3 is post-launch; deferred per [[project_post_launch_roadmap_may_to_nfl]]
- Related: [[project_dynamic_cohort_framework_607]], [[project_model_architecture_xgboost_role]], [[project_attribution_backtest_608]]
