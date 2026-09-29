---
name: prop-tier-ux-confusion-720
description: "PRIME/STRONG/LEAN badges mean different math per prop_type — PRIME 68 outs_under vs PRIME 82 hits_over both display identically. Fix needed pre-launch."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-20T19:00:16.040Z
---

**Set 2026-07-20 after user surfaced confusion around "PRIME 68" display.**

## The problem

`_tier_for_raw()` in [generate_props.py:279-353](mlb_pipeline/generate_props.py) has per-prop-type PRIME thresholds:

| Prop type | PRIME threshold |
|---|---|
| outs_under | **65** |
| ha_under · bb_over · bb_under · ha_over | **70** |
| ks_over · ks_under · hits_over · er_over · er_under · outs_over | **82** |

So conviction 68 = PRIME (outs_under only). User sees same PRIME badge and assumes same "high conviction" meaning across prop types. It doesn't.

Every threshold was calibration-justified individually (outs_under 92-97% hit rate needed lower gate to surface at all; ha_under STRONG/LEAN collapsed to SKIP after losing on 90d). Math is right, UX is wrong.

## Why this matters pre-launch

Users pattern-match on badge color/label. A "PRIME 68" outs_under next to a "PRIME 82" hits_over reads as the same conviction. Users will overweight the low-numeric PRIME and lose trust when it looks like inflation.

## Fix options (recommended path first)

1. **Show calibration context under the tier** (smallest change, biggest impact):
   - Instead of "PRIME 68" show something like "PRIME · 92% class hit rate" or "PRIME · top 15% outs_under"
   - Just the tier + class calibration % from `prop_edge_calibration` table
   - No scoring changes required, purely a rendering fix

2. **Normalize conviction to 0-100 per prop type**:
   - Rescale each prop's raw conviction so PRIME = 82+ across all types
   - Requires scoring changes + backtest revalidation

3. **Rebrand per prop family**:
   - Use SMASH/CLIP/SKIP for outs_under; PRIME/STRONG/LEAN for classic props
   - Adds nomenclature complexity — probably worse UX

## Recommendation

**Option 1 for launch.** Adds "class hit rate" text next to badge so PRIME 68 outs_under reads as "PRIME · 92% class hit rate" (highest trust surface) while PRIME 82 hits_over reads as "PRIME · 58% class hit rate" (calibrates expectations).

Data source: `prop_edge_calibration` table already stores hit% per (prop_type, tier). Just needs to render.

## Related

- [[project_prop_tier_x_type_615]] — earlier tier×prop_type audit
- [[project_ha_under_conviction_band_720]] — 30d HA_UNDER conviction band audit (85+ vs 75-84 disparity)
- [[project_prop_edge_calibration_july]] — KEEP/KILL bucket calibration table
- [[project_launch_priorities_july]] — pre-launch blockers + high-value items
