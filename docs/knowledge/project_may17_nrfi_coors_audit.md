---
name: may-17-nrfi-stratification-and-coors-audit
description: "NRFI 95+ L3-ERA stratification hypothesis REJECTED — both ≤2.50 hits at 50%, no meaningful subset. Coors disagreement audit deferred (n=1, insufficient data)."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

## NRFI 95+ L3-ERA stratification — REJECTED

5/15 hypothesis was that NRFI 95+ games with BOTH starters' L3 ERA ≤ 2.50 would outperform the cohort baseline. Validation result on n=71 resolved 95+ games:

| Subset | W-L | Hit % | n |
|---|---|---|---|
| **Both L3 ERA ≤ 2.50** | 7-7 | **50.0%** | 14 |
| Either L3 ERA ≤ 2.50 | 14-12 | 53.8% | 26 |
| Neither (or null) | 13-18 | 41.9% | 31 |
| ALL 95+ baseline | 34-37 | **47.9%** | 71 |

**The "elite both L3" subset hits at 50% — basically baseline.** Not a meaningful gate. Yesterday's NYY/NYM 100 hitting (Schlittler L3 0.51 + Holmes L3 1.47) was variance, not signal.

**Action:** Do NOT build the NRFI 95+ stratification feature. Continue treating 95+ as the trap zone — surface as informational only, never as primary play.

## Coors / high-environment total disagreement — DEFERRED

Hypothesis: at park_run_factor ≥115, when `model_pred_total` and xera-based `projected_total` disagree by ≥1 run, the runs model has been more accurate.

**Data state:**
- 100 high-env (park ≥110) games resolved in DB
- Only 1 has both fields populated with material disagreement
- `model_pred_total` is a recent field — historical resolved rows don't have it

**Defer:** Re-audit in 2-3 weeks when enough disagreement data accumulates. The 5/16 ARI/COL data point (runs model said UNDER 9.88, actual 10) matches the hypothesis but n=1 is not enough.

## Practical implications for today

- POTD SD/SEA NRFI 90 is sweet-spot band (90-94, 72.4% cohort) — NOT in the rejected 95+ zone, full conviction holds
- ARI/COL on the slate at YRFI 23 (low NRFI score) — separate cohort, separate signal

## Related
- [[project_may15_calibration_notes]] — the 5/17 audit docket where these were queued
- [[project_may17_confluence_audit]] — same-day audit, PRIME confluence degradation
- [[project_may17_hits_under_audit]] — same-day audit, STRONG > PRIME finding
