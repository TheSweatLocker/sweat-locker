---
name: may30-potd-bugs-and-gap
description: "5/30 surfaced two NRFI-leakage bugs (over_lean xERA-gap-boost overriding v3 soft UNDER; VALUE POTD fallback ignoring NRFI demotion) AND an architectural gap (POTD selector reads legacy lean_display, not sweat_dimensions.model_play)."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

5/30 PHI @ LAD chase exposed three issues stacked together. Two fixed and shipped, one architectural change queued.

**Bug 1: over_lean xERA-gap boost overriding v3 soft UNDER lean** (fixed in [`mlb_pipeline/game_context.py`](mlb_pipeline/game_context.py) line ~3085, commit `9cc8fc8`)
- PHI/LAD: v3 projected_total 7.9 vs market 8.5 = delta -0.6 (soft UNDER)
- v3 stats model set `over_lean = None` because |delta| < 1.5 (the OVER/UNDER threshold for explicit flag)
- xERA gap boost (Luzardo 2.69 vs Sasaki 4.95 = gap 2.26) checked `over_lean is None` and flipped to True
- `None` catches BOTH "v3 truly neutral" AND "v3 soft UNDER" — the gate was wrong
- 58.2% xERA-gap-OVER audit was measured on games where v3 was truly neutral or already pointing OVER; applying it against a soft-UNDER lean is outside the cohort
- **Fix:** gate the boost on `not v3_soft_under` where v3_soft_under = projected_total < total_line - 0.3

**Bug 2: VALUE POTD fallback ignoring NRFI demotion** (fixed in [`mlb_pipeline/play_of_day.py`](mlb_pipeline/play_of_day.py) line ~1570, commit `90f2265`)
- NRFI demotion 5/29 removed NRFI from compute_primary_play PRIME block AND moved it to last-resort in build_lean
- But the VALUE POTD fallback (when no audit-validated cohort qualifies) considered ANY MLB candidate with a lean_display and sweat ≥50
- NRFI candidates STILL have lean_display strings ("NRFI - Score 90/100 (supplementary)") from build_lean's fallback
- So the value fallback re-selected NRFI candidates that the audit filter had just rejected
- **Fix:** added `lean_bet not in ('nrfi', 'yrfi')` filter to value_pool

**Architectural Gap (queued for next morning): POTD selector reads `lean_display` (legacy string from build_lean), not `sweat_dimensions.model_play`**
- The 5/29 sweat dimensional redesign produces `sweat_breakdown.dimensions.model_play` per game with structured {type, label, edge, tier}
- PHI/LAD 5/30: sweat dim said winning_dimension='total', model_play={'type':'TOTAL_UNDER', 'label':'Under 8.5', 'edge':-0.6}, total tier STRONG 68
- POTD selector never saw this because it walks `lean_display` from `build_lean` which has its own priority chain (v2/RL/v4/v3-over_lean/NRFI-fallback)
- Result: after both bugs above were fixed, POTD picked TOR/BAL Over 7.5 (sweat 54) when PHI/LAD Under 8.5 (sweat 68) was the strongest non-NRFI play
- **Tonight resolved by manual override** ([`mlb_pipeline/_override_potd_phi_lad.py`](mlb_pipeline/_override_potd_phi_lad.py)) with `manualOverride: true` flag
- **Permanent fix needed:** wire `sweat_dimensions.model_play` into the POTD candidate pool. Candidates should carry the dimensional read alongside lean_display so the selector compares both. Priority: dimensional play if winning_dimension tier ≥ STRONG, else fall through to legacy lean_display

**Pattern for tomorrow:**
1. Add `dim_model_play` to candidate dict in run() at line ~903 — pulled from ctx.sweat_breakdown.dimensions.model_play
2. In the value-fallback path, rank candidates by max(legacy_score, dim_total_score) and use whichever play that came from
3. Audit-filter still uses cohort gates, but value-fallback now considers dimensional play strings
4. Re-test against the 5/29 + 5/30 slates as regression

Related: [[sweat-dimensional-redesign]] (the new scorer that exposed the gap), [[nrfi-demotion]] (the demotion these bugs were leaking around), [[v4-over-drift]] (why v3/v4 cross-model gates exist).
