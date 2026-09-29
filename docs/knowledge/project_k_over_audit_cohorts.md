---
name: K-Over alt-line + total/bullpen correlation cohorts (2026-05-10)
description: Two new audit cohort families for K-Over props — alt-line magnitude grid + total/bullpen correlation. Total correlation already showing 80% UNDER vs 54% OVER spread.
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
Shipped two K-Over audit cohort families on 2026-05-10:

**Scope #1 — Alt-line magnitude grid** (`k_over_proj_<bucket>_clears_<line>`):
- Buckets: `6_to_7`, `7_to_8`, `8_to_9`, `9_plus` (by model's `_projected_ks`)
- Thresholds: cleared 5.5 / 6.5 / 7.5 / 8.5 (book-line equivalents)
- Output: 4×4 grid in audit console, all cells upserted to `mlb_tier_calibration`
- **Status:** framework correct, data-starved. `_projected_ks` only added to signals on 2026-05-08, so existing 51 resolved K Overs predate it. Will populate within 3-7 days as new K Overs resolve.

**Scope #2 — Total + bullpen correlation** (`k_over_with_total_*`, `k_over_starter_pen_*`):
- Total correlation: WORKING immediately — joins to `mlb_game_results` (persistent table)
- Bullpen correlation: needed snapshot fix (mlb_game_context is transient)

**🎯 HEADLINE FINDING (n=39 STD):**
- **k_over_with_total_under: 20-5 (80.0%)** — K Over hits 80% when game total UNDER
- **k_over_with_total_over: 13-11 (54.2%)** — K Over hits 54% when game total OVER
- **26 percentage-point spread** validates the hypothesis: low-total games = pitcher's duel = starter goes deep = K Over hits.

**How to apply:** When tonight's POTD is total UNDER (like 5/10 Braves/Dodgers UNDER 9.0), K Over plays in low-total games get a conviction boost above their tier baseline. Conversely, K Over PRIMEs in OVER environments should be downsized.

**Bullpen snapshot fix:** `generate_props.py:score_pitcher_ks` now writes `signals._starter_pen_relievers_3d` at pick time. Audit reads from snapshot first, falls back to `mlb_game_context` join. Bullpen correlation will populate going forward.

**File locations:**
- Cohort logic: `mlb_pipeline/audit_tier_calibration.py` — `fetch_all_resolved_ks_over_props()`, `compute_k_over_alt_window_rates()`, `compute_k_over_correlation_rates()`, `fetch_resolved_game_results_map()`, `fetch_game_context_pen_map()`
- Snapshot: `mlb_pipeline/generate_props.py:score_pitcher_ks` writes `_projected_ks` + `_starter_pen_relievers_3d` to signals

**Next checkpoint:** Re-read alt-line cohort numbers ~2026-05-17 when 30+ new K Overs have accumulated with `_projected_ks`. Adjust pipeline prop_line cap if cohort shows specific magnitude band underperforming.
