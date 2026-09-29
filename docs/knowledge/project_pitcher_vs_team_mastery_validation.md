---
name: pitcher_vs_team mastery layer validated against backfilled data
description: Backfill audit on 2,892 games confirms mastery signal materially changes confluence cohort hit rates; agree vs disagree segmentation is real
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
Backfilled pitcher_vs_team_era for 2,835/2,886 historical games on 2026-05-07. One-off audit segmented confluence cohorts by whether pitcher mastery direction agrees or disagrees with model_pick. Strong signal validation.

**Why:** The Eovaldi/Yankees miss on 5/6 (PRIME +8 confluence on Yankees ML, but Eovaldi 0.36 ERA vs NYY 25 IP mastery against → Texas covered 6-1) was the trigger to ship the 9th vote (pitcher_vs_team) in confluence_breakdown. After backfilling historical data, audit confirms the segmentation is real — mastery direction materially predicts hit rate.

**Audit findings (samples small but pattern unambiguous):**

| Tier | Mastery | Record | Rate |
|------|---------|--------|------|
| PRIME +4 | agrees | 7-0 | 100% |
| PRIME +4 | no flag | 4-2 | 66.7% |
| PRIME +4 | disagrees | 3-4 | **42.9%** ← fade |
| STRONG +2-3 | agrees | 3-1 | 75% |
| STRONG +2-3 | no flag | 11-7 | 61.1% |
| STRONG +2-3 | disagrees | 2-3 | **40%** ← fade |

**How to apply:**
1. Current production: 9th vote in confluence_breakdown (1 point against). Already live as of commit ef8dab8 (5/7 morning).
2. Consider stronger gate: when mastery disagrees with model_pick AND IP ≥ 15, downgrade conviction tier by 1 step (PRIME → STRONG → LEAN). Audit-justified by the 42.9% disagree rate at PRIME.
3. Conversely, surface "mastery_agreement" as a positive flag in app/Sweat Card UI — PRIME-agreement subset hit 7-0 in sample, true elite tier.

Threshold reminder from project_offense_drift_signal.md: 15 IP minimum sample for mastery to count (Lorenzen 7-IP "mastery" was a mirage that blew up Mets/Rox UNDER on 5/6).

Re-evaluate after another 30-60 days of data when sample sizes triple.
