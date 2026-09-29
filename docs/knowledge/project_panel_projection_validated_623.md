---
name: project-panel-projection-validated-623
description: 6/23 Panel ER projection beats naive xERA+L3 math 57% vs 52% direction; keep Panel, don't replace
metadata:
  type: project
---

User question 6/23: are Numbers Panel pitcher projections (Avila 0.9 ER etc.) actually performing well or should we replace them?

**Audited 980 graded historical pitcher props:**
- ER: direction 58% (n=74), MAE 1.64, bias near zero — REAL EDGE
- Ks: direction 56% (n=302), PRIME tier hits 67%
- BB: direction 61% (n=276) — best calibrated
- Hits: direction 50% — weakest, coinflip
- Outs: looked broken (MAE 12.86) but was a GRADING bug not projection bug — see [[project_outs_grading_bug_fix_623]]

**Head-to-head vs 50/50 xERA+L3 math (n=58 ER):**
- Panel: 57% direction (33/58), MAE 1.69
- Math: 52% direction (30/58), MAE 1.67
- When disagree (n=11): Panel right 64%, Math right 36%
- Ensemble (average): no improvement over Panel alone

**Calibration finding:** Panel regresses extremes toward the mean
- Elite projections (<1.5 ER) actual avg 1.71 — under-projected by 0.85
- Solid (1.5-2.5) actual avg 2.75 — under-projected by 0.97
- Average (2.5-3.5) actual avg 2.00 — over-projected by 1.06
- Poor (3.5+) actual avg 3.81 — close

So Avila 0.9 ER projection is probably actually 1.5-2.0 ER expected, NOT the 3.1 my naive math suggested.

**How to apply:** Trust the Panel projections. Don't replace with raw xERA+L3 blend. If proposing changes to projection logic, target the regression-toward-mean effect (slight L3 reweighting at extremes), don't blow it up.

Related: [[project_outs_grading_bug_fix_623]], [[project_total_v7_shadow_shipped_623]]
