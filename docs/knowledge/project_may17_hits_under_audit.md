---
name: may-17-hits-under-tier-audit
description: Hits-UNDER STRONG tier (64.6%) outperforms PRIME tier (56.8%) on n=156. PRIME gate is over-promoting. opp_k_artist signal alone is strongest predictor (64.1%).
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

## Headline

**STRONG hits_under outperforms PRIME hits_under by 7.8pts** — but the scorer keeps promoting to PRIME using gates that don't deliver. Vlad Jr's 5/16 "PRIME tier capped" output was the system catching this in real time.

| Tier | STD W-L | % | n |
|---|---|---|---|
| STRONG | 53-29 | **64.6%** | 82 |
| PRIME | 42-32 | **56.8%** | 74 |
| SKIP | 7-6 | 53.8% | 13 |

## Signal-level diagnosis (within PRIME picks, n=74)

| Signal | Hit rate | n | Verdict |
|---|---|---|---|
| `opp_k_artist` (opp starter K% ≥30%) | **64.1%** | 39 | **Strongest standalone** |
| `hitless_streak` (consecutive 0-hit games) | 61.3% | 31 | Solid |
| `l7_cold` (L7 BA <.150) | 60.0% | 45 | Solid |
| `l7_avg_cold` | 57.7% | 52 | Modest |
| `opp_form_hot` | 56.2% | 32 | Modest |
| `opp_k_heavy` | 51.9% | 27 | Coin flip |
| `lineup_spot` (batting 8-9) | 50.0% | 44 | **Noise** |
| `team_offense` | 45.0% | 20 | Below baseline |

`lineup_spot` and `team_offense` are pure noise. When PRIME promotion is driven by these, the prop underperforms.

## Recommended scorer change (post-launch)

Tighten the PRIME gate to require:
- **`opp_k_artist` OR `hitless_streak` (≥3 games)** as the primary anchor
- Plus EITHER `l7_cold` OR `opp_form_hot`
- `lineup_spot` and `team_offense` count toward score but don't trip PRIME promotion alone

Expected effect: roughly half of current PRIMEs get demoted to STRONG, but the remaining PRIME cohort should land at 65-70%.

## Why this matters now

- Today's slate has no PRIME hits-unders, so no immediate card impact
- 5/16 was 10-4 on PRIME hits-unders (71%) — outperformed the cohort average, lucky on noise-driven picks
- Long-term, the over-promotion eats into our public-card credibility when picks at "PRIME" tier go 0-3 the next day

## Related
- [[project_may17_confluence_audit]] — same-day audit, similar story (PRIME tier shrinking)
- [[project_may15_calibration_notes]] — 5/17 audit docket
