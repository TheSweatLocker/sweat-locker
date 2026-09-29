---
name: potd-audit-queued-607
description: "POTD audit queued (user flagged 6/7 evening) — 2-4 in last 6, mostly ML picks. Run in a few days once we have more sample. Likely culprit: POTD selector anchored on a model whose ML output is cold while its totals output is hot."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

User flagged 2026-06-07 evening: POTD has gone **2-4 in last 6, picks have been mostly ML**. Wants an audit "in a couple days" — wait for sample to grow, then dig.

**Why this is worth a focused audit (not just variance):**
- ML at 2-4 = 33% with bias toward one play type is a SELECTION pattern, not a randomness pattern
- POTD selector lives in `play_of_day.py` — anchors the public daily-best-bet
- Per 6/7 model perf: v4 ML went 2-3 (40%), Jerry ML 6-5 (55%), v3 ML 6-3 (67%), Confluence ML 5-2 (71%)
- If POTD selector weights v4 + Jerry ML heavily (which were the cold ML days), it would skew toward bad ML picks even when totals/RL plays from the same models were hot
- Today's Jerry was 6-5 ML but 3-0 totals on 6/6. POTD-by-Jerry on ML days = brutal; POTD-by-Jerry on totals days = great

**What to look at when the audit fires:**
1. Pull last 14d of POTD picks from `daily_best_bet_history` → group by play_type (ML / RL / Total / NRFI / Prop)
2. Per-type win rate. If ML is significantly underperforming RL/Total on the same slates, the POTD selector is over-weighting ML
3. Cross-reference each POTD pick with what the other models were saying — was POTD picking the model with the WORST agreement for that day?
4. Check `_select_potd()` (or equivalent) in `play_of_day.py` — what's the scoring logic? Is it model-agnostic or hardcoded to prefer ML when conviction is high?
5. Consider per-day-model weighting (see [[model-of-the-day-weighting]] — same conceptual fix applies here)

**Tied to other docket items:** Model-of-the-day weighting + POTD selector refactor are essentially the same fix surface area — once we know which model is hot on which days, the POTD picker should follow that. Phase 2 effort, not tonight.

Linked: [[project_dynamic_cohort_framework_607]] (the same "cite live data, not stale snapshot" pattern applies to model performance weighting), [[project_postponement_api_truth_607]] (parallel example of pushing inference out to the source of truth).
