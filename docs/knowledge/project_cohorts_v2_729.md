---
name: cohorts-v2-729
description: "7/29 sprint: v2 cohort family (7 signals) shadowing v1. Backtest n=1543 shows 5 keepers + |v2_net|=3 hits 75% n=28. Live compute writes to mlb_game_context.signal_confluence_v2_breakdown/net. Grade for 30d then decide promotion."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-29T23:23:29.665Z
---

**Shipped 2026-07-29 as v1-preserving shadow signal.**

## Design

v1 (signal_confluence_breakdown/_net) UNTOUCHED per user 2026-07-29 explicit ask
("As long as we can still identify patterns between v1 and v2").
v2 runs parallel — same games, different cohort family — for 30d shadow grade
before any tier-decision consumption.

## v2 cohort family (files: cohorts_v2.py)

| Cohort | Rate | n | Status |
|---|---|---|---|
| west_east_early | 57.0% | 235 | ⭐ HOME-only signal (fades west traveler) |
| ace_hot_offense | 55.9% | 143 | ✓ |
| starter_rest_extreme | 54.8% | 343 | ✓ **INVERTED** from 45.2% — extreme rest = strength |
| fresh_vs_grind | 53.3% | 152 | ✓ |
| bp_fatigue_combo | 50.3% | 835 | Marginal alone; stacking value |
| series_letdown | 0 fires | 0 | Defer — series-matchup detection broken |
| ~cold_after_blowout~ | 42.9% | 14 | ✗ DISABLED (too rare, weak) |

## v2_net magnitude backtest

| \|net\| | Record | Rate | n |
|---|---|---|---|
| 1 | 379-371 | 50.5% | 750 |
| 2 | 109-88 | 55.3% | 197 |
| **3** | **21-7** | **75.0%** ⭐⭐ | **28** |
| 4 | 2-1 | 66.7% | 3 |

Compared to v1's |net|=4 at 75% n=20 — v2 reaches 75% at |net|=3 (one cohort
earlier), suggesting v2 has less trap-cohort pollution.

## Files

- `mlb_pipeline/cohorts_v2.py` — 7 cohort compute functions (5 active)
- `mlb_pipeline/backtest_cohorts_v2.py` — historical grader vs mlb_game_results
- `mlb_pipeline/compute_cohorts_v2.py` — live nightly writer (reads mlb_game_results, writes mlb_game_context)
- `mlb_pipeline/compare_v1_v2_nightly.py` — post-game v1-vs-v2 grader
- `supabase/migrations/20260729c_cohorts_v2.sql` — schema (applied 7/29)

## Nightly runbook

```bash
# ~5:30 PM ET after pull_externals_mlb finishes:
python mlb_pipeline/compute_cohorts_v2.py

# ~2 AM ET next morning after game results resolve:
python mlb_pipeline/compare_v1_v2_nightly.py --date <yesterday>
python mlb_pipeline/compare_v1_v2_nightly.py --lookback 14  # rolling window
```

## Decision gates (30-day promotion review)

At 30d of shadow data (target 2026-08-29):

- If v2 disagree-wins < v1 disagree-wins → v1 stays king, kill v2 or refine
- If v2 disagree-wins ≥ v1 disagree-wins by ≥8pt margin → promote v2 as
  primary tier driver (v1 becomes shadow)
- If ties → keep both, compute a BLENDED net (v1 + v2 half-weight)

## Deferred

- Fix series_letdown same-series detection
- Add stat-driven cohorts (soft-tosser vs high-K, GB-pitcher-in-FB-park)
- Sport-agnostic pattern → NFL/NCAAF/NCAAB cohort_v2 families (all data pipes present)

## Related
- [[project_cohort_inversion_729]] — v1 fixes (h2h_recent_home invert etc.)
- [[project_confluence_net3_trap_729]] — v1 |net|=3 trap zone
- [[project_align_status_729]] — parallel alignment layer that lives alongside cohorts_v2
- [[project_game_detail_redesign_729]] — where v2 signals surface in app
- [[feedback_ml_vs_rl_conflation]] — the discipline that lets us run v1+v2 cleanly
