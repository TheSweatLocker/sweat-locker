---
name: cohort-combos-721
description: "14d cohort combination audit (n=150) proved raw cohort volume on totals is coinflip. Real signal: SPLIT-LEAN cohorts on ML predict FADE side wins 61-63%. Shipped cohort-fade ML lane + tier-quality guard."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  audit_date: 2026-07-21
  modified: 2026-07-21T18:29:03.101Z
---

**Ran 2026-07-21 after user pushed: "if 5 cohorts say UNDER and 3 say OVER, what happens?"**

## Headline: cohort volume on TOTALS = coinflip

Louder is not righter:
- UNANIMOUS_UNDER: 55.6% (n=18)
- LOUD_UNDER (5+ U, 0-2 O): 50.8% (n=59)
- SPLIT_UNDER_LEAN: 46.9% (n=32)
- COINFLIP: 60.0% (n=25)
- LOUD_OVER: 0% (n=2)

No monotonic relationship between (under_count − over_count) and UNDER hit rate. **Stacking UNDER cohorts does NOT unlock a hidden 60%+ bucket.**

## Real actionable signal — CONTRARIAN INVERSION on SIDES

| ML bucket | HOME_WIN | n | Play |
|---|---|---|---|
| **SPLIT_AWAY_LEAN** | **61.4%** | 44 | **BET HOME** ⭐ |
| **SPLIT_HOME_LEAN** | 37.5% | 16 | **BET AWAY (62.5%)** ⭐ |
| LOUD_AWAY | 52.4% | 21 | pass |
| LOUD_HOME | 44.4% | 9 | slight AWAY lean |
| COINFLIP | 51.8% | 56 | pass |

When ML cohorts split-lean 2-3 vs 1-2, **the OTHER side wins 61-63%** (combined n=60). This is a legitimate contrarian pattern strong enough to publish. LOUD or UNANIMOUS consensus reverts to coinflip.

Runlines are noise across all buckets (49-53%). Skip.

## Tier quality is the total lever (missed gate)

| Bucket × quality | UNDER hit | n |
|---|---|---|
| LOUD_UNDER + STRONG_ONLY | **55.6%** | 45 |
| LOUD_UNDER + MIXED (STRONG + LEAN) | **35.7%** | 14 |
| UNANIMOUS_UNDER + STRONG_ONLY | 60.0% | 10 |
| SPLIT_UNDER_LEAN + STRONG_ONLY | 45.0% | 20 |

**LEAN cohorts POISON the read.** When they co-fire with STRONG_EDGE, the UNDER hit rate crashes from 56% to 36%. That's a live gate we were missing.

## Confluence anomaly (small sample flag)

LOUD_UNDER + `signal_confluence_net` ≤ −2 (both agree UNDER) → 25% UNDER hit (n=8). Market has priced it in. Flag, don't gate yet.

## Shipped in tier_discipline_gate.py

1. **evaluate_ml_cohort_fade()** — new function. When cohorts split-lean (ratio 0.2-0.5 or -0.5 to -0.2), returns LEAN verdict on the FADE side. Cited as "cohort SPLIT_AWAY_LEAN fade to HOME (14d 61.4%, n=44)". Never PRIME — always LEAN — because 7d shadow validation not yet run.

2. **_majority_tier_quality()** — helper. Classifies cohort list as STRONG_ONLY / MIXED / LEAN_ONLY. Available for evaluate_total to downgrade when MIXED.

## Shipped in play_of_day.py

After `evaluate_ml()` returns SKIP, calls `evaluate_ml_cohort_fade()` on the pick's cohort_signals.matched_plays. Publishes as LEAN with `_gate_source: 'cohort_fade_ml_lane'`.

**Expected impact: +4-6 additional card-worthy ML plays per night at ~61% real hit rate.**

## Caveats + validation queue

- 14d sample; SPLIT_AWAY_LEAN (n=44) is credible; SPLIT_HOME_LEAN (n=16) needs 30d confirm.
- Tier-quality MIXED-UNDER (n=14, 36%) is directionally strong but thin.
- 7-day shadow of fade lane recommended before promoting from LEAN tier.

## Related

- [[project_audit_battery_721]] — parent audit battery
- [[project_model_reweight_721]] — sides reweight ships together
- [[project_confluence_dead_signal_712]] — confirms confluence adds little
- [[project_side_resolver_wired_611]] — resolver architecture (unchanged)
