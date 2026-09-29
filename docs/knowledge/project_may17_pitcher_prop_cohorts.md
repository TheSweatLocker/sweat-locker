---
name: may-17-pitcher-prop-cohort-audit
description: Outs UNDER STRONG is 10-0 lifetime (loudest cohort). ER OVER PRIME is 1-3 (25%) — flag for scorer. Outs OVER PRIME/STRONG is 0-2. 1st-inn ERA ≥8 only 50.7% YRFI — not standalone signal.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

## Headline findings (small samples but directional)

| Cohort | W-L-P | Hit % | n | Verdict |
|---|---|---|---|---|
| **Outs UNDER STRONG** | 10-0-0 | **100%** | 10 | 🟢 Loudest cohort |
| BB UNDER PRIME | 3-1 | 75% | 4 | 🟢 Small but positive |
| BB OVER PRIME | 3-1 | 75% | 4 | 🟢 Small but positive |
| HA UNDER PRIME | 3-2 | 60% | 5 | 🟢 Modest |
| ER UNDER PRIME | 2-0 | 100% | 2 | 🟢 Tiny but clean |
| **ER OVER PRIME** | **1-3** | **25%** | 4 | 🔴 Yellow flag |
| ER OVER STRONG | 4-5 | 44% | 9 | 🟡 Below baseline |
| **Outs OVER PRIME/STRONG** | **0-2** | **0%** | 2 | 🔴 Skip until validated |

## Game-level cohorts (larger samples)

| Cohort | Hit % | n | Notes |
|---|---|---|---|
| **xERA gap 2-3 → OVER** | 59.7% | 63 | Validated, scorer already fires |
| xERA gap ≥3 → OVER | 56.5% | 24 | Same direction |
| max_1st_ERA ≥8 → YRFI | 50.7% | 71 | Coin flip — NOT standalone YRFI signal |

## Actionable for scorer

### 1. ER OVER scoring needs review
- PRIME tier hits 25% (n=4) — too small to retune from, but direction worrying
- Combined with yesterday's Alcantara ER OVER STRONG loss (cohort drops to 5-5)
- **Don't promote ER OVER to PRIME via single signal** — needs multiple stacked anti-mastery indicators

### 2. Outs UNDER is the cleanest pitcher-prop direction
- STRONG cohort 10-0 lifetime — every single one cashed
- **Should be the anchor expression for "pitcher gets hooked early" theses**, not ER OVER
- Bello (today) is in this cohort at STRONG 80 — anchor him there

### 3. Outs OVER should be skipped publicly
- 0-2 PRIME/STRONG lifetime + 0-1 STRONG yesterday (Elder) = 0-3 overall
- Until n=10+ validation, treat Outs OVER as personal play only

### 4. 1st-inn ERA needs companion signals
- Standalone fragility doesn't predict YRFI (50.7% at ≥8 ERA)
- Combine with NRFI score, opp lineup wRC+, or recency for actionable signal

## Today's specific application

| Prop | Cohort | Recommendation |
|---|---|---|
| Bello Outs UNDER STRONG 80 | 10-0 lifetime | **ANCHOR Card 2** |
| Bello ER OVER PRIME 100 | 1-3 lifetime | Half-stake; keep in card but not as anchor |
| Holmes HA UNDER PRIME 76 | 3-2 lifetime | Card-worthy, correlated with ATL ML |
| Lorenzen Outs UNDER PRIME 82 | no PRIME cohort yet | Strong personal play; STRONG cohort backs direction |
| McDonald BB UNDER PRIME 81 | 3-1 lifetime | Card-worthy |
| Gilbert BB UNDER PRIME 75 | 3-1 lifetime | Personal play (lower conviction) |

## Related
- [[project_may17_confluence_audit]] — same-day, PRIME confluence degraded
- [[project_may17_hits_under_audit]] — same-day, STRONG > PRIME pattern
- [[project_may17_nrfi_coors_audit]] — same-day, 95+ stratification rejected
