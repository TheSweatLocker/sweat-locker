---
name: project-nfl-k-pts-calibration-bug-912
description: "RESOLVED 2026-09-16 — NFL projected_spread swapped to v2 (pred_delta-based, calibrated on 315 graded games). K_PTS v1 formula deprecated but kept as legacy in historical rows."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-17T16:02:52.365Z
---

**✅ RESOLVED 2026-09-16.** `nfl_game_context.compute_projections` (line 776) now
returns `projected_spread = projected_spread_v2 = round(0.80 × pred_delta_home + 0.90, 2)`.
The v2 formula uses per-team matchup-adjusted points (LEAGUE_AVG_PPG × off_idx ×
opp_def_idx) instead of the collapsed `power_diff × K_PTS + HFA` v1 form. Calibrated
against 315 graded games: slope 0.80, intercept 0.90, correlation 0.51 vs 0.46 for
raw off_rating_diff.

**Verified 2026-09-17 on Wk3 slate (16 games)**: projected_spread range [-3.80, +11.03]
= 14.83-pt band. Prior v1 collapsed to HFA±0.15 (2.3-pt band). Spot check DET@BUF:
pred_h=27.8, pred_a=25.6 → delta +2.2 → v2 = 0.80×2.2 + 0.90 = 2.66 ✓ exact match.

**Storage**: no `projected_spread_legacy` column in schema. Historical rows have v1
values frozen at write time. Going forward `projected_spread` is v2.
`projected_spread_raw` (pre-anchor v2) still set by projection_anchor caller.

**Ensemble impact**: reads `projected_spread` unchanged, but now sees real
projection instead of Vegas-with-noise. Market anchor still fires on >6pt
disagreement (pulls toward market). On smaller (≤3pt) disagreements, the model
gets its own voice.

**NCAAF check (verified 2026-09-17)**: `ncaaf_game_context.compute_projections`
line 579 uses `projected_spread = sp_gap × K_PTS_SP + hfa` where K_PTS_SP=0.85 and
SP+ is already a point-differential rating. Wk4 Sat slate (53 games w/ projections):
range [-19.69, +45.33], median +10.41, well-distributed across 8 buckets. Not the
same bug class — SP+ input carries real signal, K_PTS_SP=0.85 preserves it. No fix
needed.
