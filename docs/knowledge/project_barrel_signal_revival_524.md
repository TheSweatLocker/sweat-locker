---
name: barrel-signal-revival-524
description: "Barrel% slump detector was dead code for 12 days (Savant column rename); revived 2026-05-24, sample collecting now"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Barrel% slump detector originally shipped 2026-05-11 (4 signals across
`score_batter_hits` and `score_batter_hits_under` — see
[[project_barrel_and_bb_h_signals]]). Audit on 2026-05-24 found ZERO of
244 resolved hits_under and ZERO of 263 resolved hits_over picks fired
ANY of the four signals.

Root cause: Savant renamed leaderboard columns between 5/11 and 5/24:
  `barrel_batted_rate` → `brl_percent`
  `hard_hit_percent`   → `ev95percent`

`fetch_batter_quality()` silently returned {barrel_pct: None,
hard_hit_pct: None} for every batter. Every guard `if quality and
quality.get('barrel_pct') is not None` failed, so the regression-detector
boost/fade was never applied to any pick.

Fix shipped commit `1069096`:
- Prefer new column names, keep legacy as fallback
- Added load-time print: "Loaded ... for X batters (Y with non-null barrel%)"
- Added schema-break guard that warns "Savant returned 0 batters with
  barrel_pct — column rename suspected, signals will be dead"

**Why:** Prevents the same 12-day silent-failure pattern. Pairs with
[[feedback_migration_pgrst_reload]] — both are cases of upstream schema
changes silently breaking pipeline features.

**How to apply:** If hits_over / hits_under PRIME hit-rate moves
significantly in the next 2 weeks, attribute partly to barrel detector
now actually firing. If it doesn't move, the 5/11 conviction weights
(-6 / +4 / +6 / +3) may be set too small; tune at next audit (queued for
2026-06-07 with n>=20 per signal).

**Distribution today (262 qualified batters):**
- median brl% 8.3, p25 5.3, p75 11.7
- ≥10% (elite tier for `barrel_due`): 34% of batters
- ≥9% (above-avg tier for `barrel_regression`): 42%
- ≤4% (no-quality tier for `barrel_genuinely_cold`): 17%

Thresholds still map correctly to the intended tiers.
