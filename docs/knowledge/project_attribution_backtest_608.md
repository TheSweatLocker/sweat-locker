---
name: attribution-backtest-608
description: "6/8 commit 1fc7643: 4-pass model attribution backtest over 518 graded mlb_game_results. Discovered data-driven cohort rules — strongest is 'v3 tot loud + away SP form drift = 17-0 (100%)'. NOT YET WIRED. User wants recency check + inverse analysis before deploying. This is the rigorous version of cohort discovery — preserve carefully."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

User asked: "How do we know what the model of the day is though? Is there not a way to backtest what holds the most weight, what could be driving correct predictions, there has to be a pattern right to get better results."

Built four-pass attribution backtest (commit `1fc7643`):
- `backtest_model_attribution.py` — v1 broad sweep, 199 cohorts
- `backtest_model_attribution_v2.py` — direction-conditional + 2D interactions + NRFI/YRFI
- `backtest_model_attribution_v3.py` — 3-way intersections (sample-thin, need 1500+ rows)
- `backtest_model_attribution_v4_pitcher_class.py` — per-pitcher-class deep dive

All output JSON in `mlb_pipeline/models/attribution_cohorts*.json`. Read-only. Re-runnable.

## Baselines (the floor each model has to beat over 518 graded rows)

| Play | Win % | n |
|---|---|---|
| **v3 totals** | **66.5%** | 212 — strongest single signal in entire system |
| v4 RL | 56.8% | 273 — strongest RL signal |
| v4 totals | 56.2% | 256 |
| v3 ML | 55.2% | 417 |
| v3 RL | 53.1% | 375 |
| v4 ML | 52.9% | 310 |
| Jerry ML | 52.3% | 109 |
| Jerry totals | 50.7% | 71 |
| Jerry RL | 50.6% | 89 |
| conf ML | 50.5% | 327 |
| conf RL | 48.8% | 244 |

**v3 totals is dramatically underused. Jerry's outputs are barely better than coin flip at the level layer — Jerry's value is conditional.**

## 🟢 STRONG-EDGE COHORTS (wire these into selector)

### Tier 1 — near-lock (≥85% hit rate)
| Cohort | Play | Record | Hit % |
|---|---|---|---|
| v3 tot loud + away SP form drift bad | v3 tot | **17-0** | **100%** |
| v3 tot over lean + away SP form drift bad | v4 tot over | 9-0 | 100% (small n) |
| v3+v4 totals loud consensus | v4 tot | 17-1 | 94.4% |
| v3+v4 totals loud consensus (OVER) | v4 tot over | 14-1 | 93.3% |
| v3 tot loud OVER | v3 tot over | 28-2 | 93.3% |
| v3 tot loud (combined) | v3 tot | 54-7 | 88.5% |
| v4 tot when v3 tot loud + OVER | v4 tot over | 19-3 | 86.4% |

### Tier 2 — strong edge (70-85%)
| Cohort | Play | Record | Hit % |
|---|---|---|---|
| v3 tot + away SP form drift bad | v3 tot | 30-6 | 83.3% |
| v3 tot loud + UNDER | v3 tot under | 26-5 | 83.9% |
| conf=4 + FAV (home) + ML pick | conf ML home | 16-4 | 80.0% |
| v3 tot + home SP form drift bad | v3 tot | 26-7 | 78.8% |
| v3 ML home + conf=4 | v3 ML home | 18-5 | 78.3% |
| conf=4 + FAV (home) + RL home pick | conf RL home | 15-4 | 78.9% (when away_drift+opp_hot) |
| v3 ML home + conf=4 + FAV | v3 ML home | 14-4 | 77.8% |
| v4 ML when conf=5 | v4 ML | 21-6 | 77.8% |
| v4 RL when v4_jerry_disagree away | v4 RL away | 17-5 | 77.3% |
| v4 RL when v4_jerry_disagree | v4 RL | 19-6 | 76.0% |
| v4 ML away + conf=5 | v4 ML away | 16-5 | 76.2% |
| conf=4 + ML home (general) | conf ML home | 34-11 | 75.6% |
| conf=4 + RL home (general) | conf RL home | 25-8 | 75.8% |
| v4 ML home + K-class home matchup (home high-K vs away K-prone) | v4 ML home | 11-4 | 73.3% |
| conf ML home + K-class home matchup | conf ML home | 13-4 | 76.5% |
| v4 RL when both shaky | v4 RL | 20-7 | 74.1% |
| v3 ML when conf=5 | v3 ML | 28-10 | 73.7% |
| Jerry RL away + home SP low-K | jerry RL away | 14-5 | 73.7% |
| v4 tot when v3 tot loud (combined) | v4 tot | 26-5 | 83.9% |
| v4 tot OVER when home SP form drift | v4 tot over | 27-10 | 73.0% |
| conf RL when away SP form drift bad | conf RL | 26-13 | 66.7% |
| Jerry ML away + home SP low-K | jerry ML away | 15-6 | 71.4% |
| v3 ML away + mismatch (away SP elite) | v3 ML away | 15-6 | 71.4% |
| Jerry ML away + xera gap loud | jerry ML away | 12-5 | 70.6% |
| Jerry RL away + xera gap loud | jerry RL away | 11-4 | 73.3% |
| NRFI + home SP long rest | nrfi pick high | 22-9 | 71.0% |
| v4 ML away + park hitter-friendly | v4 ML away | 24-10 | 70.6% |

## 🔴 FADE COHORTS (drop or invert these — strong negative edge)

| Cohort | Play | Record | Hit % |
|---|---|---|---|
| v4 ML away + conf=4 + DOG | v4 ML away | 1-10 | **9.1%** |
| Jerry ML home + away K-gap loud+ | jerry ML home | 1-7 | 12.5% (small n) |
| conf ML home + mismatch (away SP elite) | conf ML home | 3-13 | **18.8%** |
| v3 RL away + away SP form drift bad | v3 RL away | 4-15 | 21.1% |
| v4 ML away + conf=4 | v4 ML away | 4-15 | 21.1% |
| Jerry RL home + v4 disagrees | jerry RL home | 5-17 | 22.7% |
| Jerry RL when v4 disagrees | jerry RL | 6-19 | **24.0%** |
| v4 RL away + conf=4 | v4 RL away | 4-11 | 26.7% |
| v3 RL away + conf=4 | v3 RL away | 5-14 | 26.3% |
| Jerry RL when v3+v4 disagree | jerry RL | 6-15 | 28.6% |
| conf ML home + away K-gap loud+ | conf ML home | 13-32 | 28.9% (n=45) |
| conf ML home + v3 picks away + away K advantage | conf ML home | 11-26 | 29.7% (n=37) |
| v3 tot OVER + conf=4 | v3 tot over | 7-9 | 43.8% |
| v4 tot OVER + conf=4 | v4 tot over | 7-12 | 36.8% |
| v4 tot when conf_mag=3 | v4 tot | 14-22 | 38.9% |
| v4 ML home + away SP form_hot | v4 ML home | 7-15 | 31.8% |
| v3 tot when xera_gap_tight (≤0.3 between SPs) | v3 tot | 15-16 | 48.4% (drop from 66.5%) |
| conf RL away when away SP high-K + home team K-prone | conf RL | 4-12 | 25.0% |

## NEW Themes Discovered

1. **Direction matters as much as magnitude.** conf=4 hits 75-80% on home picks, drops to 20-30% on away picks. The cohort `conf_stats.json` showed conf=4 at 68.8% — that's the AVERAGE, but the directional split is way more useful.

2. **Pitcher mismatch (xera_gap_loud ≥1.5) is a strong predictor:** take the side WITH the better starter. Confluence gets confused by road aces.

3. **K-class matchups are directional** — home K-monster vs K-prone away = 73-77% home; away K-monster vs K-prone home = fade home.

4. **form_hot signal (L3 ERA ≤ -1.5 below xERA) matters as much as form_drift** — we're not using positive streaks at all.

5. **conf=4 is "side good, total bad"** — when conf is loud, the game tends to go UNDER (decided early). New nuance we weren't applying.

6. **Jerry sides have specific positive cohorts** (home SP low-K + away pick = 71-74%, xera_gap_loud + away pick = 70-73%). Jerry isn't broken — its value is conditional on matchup type.

7. **v3 totals at 66.5% baseline is the strongest single signal** and the form-drift booster takes it to 100% over 17 games. **Most underused signal in the system.**

8. **v4 RL is the strongest RL signal** at 56.8%; hits 74% when both SPs are shaky, 75-77% at conf=5.

## What's NEXT (per user — currently mid-dig)

Working down the list:
1. ✅ Per-pitcher-class deep dive (v4 — completed)
2. **NEXT: Recency check** — do top cohorts hold in last 30d, or some fading?
3. Inverse / middle-zone analysis (when models hit ~50%, what predicts which side?)
4. (Curiosity, not priority) Lower 3-way bar to n≥8 to see what speculative 3-way cohorts emerge

User explicit: "make sure we are keeping track of these and make sure they do not get mixed up in any form, this all seems really valuable and possibly a game changer."

## Honest assessment of value

**This is the most rigorous data work we've done.** The findings ARE actionable — wiring even half the strongest cohorts (especially v3 totals + form drift) should lift the public card hit rate materially. Realistic frame:
- 17-0 cohorts are probably true ~85-90% (regression to mean)
- Some cohorts may decay as markets adjust
- Multiple-comparisons problem: testing 100+ cohorts means some will look great by chance
- 518 rows is decent but not huge — n=15-20 cohorts are informative but not bulletproof

**Once recency + inverse digs are done, the wire-in plan is:**
1. Build a `cohort_signal_lookup.py` (like `cohort_lookup.py` but for predictive cohorts not historical %s)
2. Modify v3 tot / v4 tot / conf scorers to consult cohort lookup before final conviction
3. POTD selector reads top cohorts to filter/boost ML picks (likely fixes the 2-5 streak)
4. Sweat card filter: drop plays that hit a known fade cohort

NOT WIRED YET. User wants finishing analysis before deploying.

Linked: [[project_dynamic_cohort_framework_607]] (similar cohort-driven label refactor — same JSON-in-jerry_cache pattern is the right deployment shape), [[project_potd_audit_queued_607]] (POTD likely fixed by routing through cohort lookup), [[v4-blackout-606]] (we now have data-driven gates for what we were doing by intuition).
